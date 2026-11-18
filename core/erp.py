"""A unified local business record (R19-3, aureuserp/aureuserp inspiration).

Parent-ruled (licence, 2026-11-12): aureuserp/aureuserp (MIT,
LICENSE from master, 1,077 bytes, 19 lines by the API read, "Copyright
2010-2025, Webkul Software", read in full on the repo's LICENSE page).
Nothing is adopted — no PHP code, no Laravel, no Filament, no MySQL,
no wrapper, no port. The build takes only the ERP shapes (customers,
products, inventory, sales/purchase orders, invoices, payments, leads,
employees, projects) and rebuilds them as own stdlib code.

What is built (one effectful tool `erp`, free by design):
  * customers — add / list / get / remove (a customer with orders or
    invoices cannot be removed; the refusal names the counts).
  * leads — add / list / convert / remove (CRM: a lead converts into
    a customer; an already-converted lead is refused).
  * products — add / list / get / remove (a product on any order or
    invoice line cannot be removed). Stock is DERIVED from the stock
    movement ledger, never a stored counter — one source of truth.
  * stock — adjust (a manual correction with a reason) / list.
  * sales orders — create / list / get / fulfil / cancel. Creating
    an order does not move stock; fulfilling it decrements stock and
    is refused when any line is short, with the numbers. Cancelling
    is allowed only before fulfilment.
  * purchase orders — create / list / get / receive / cancel.
    Receiving increments stock. A received or cancelled order cannot
    be received or cancelled again.
  * invoices — create (from a sales order, or standalone for a
    customer) / list / get / void. An invoice is a RECORD the owner
    made — the money moved outside VYUK, the tool never takes
    payment (the event_tickets sale precedent). One invoice per sales
    order. The total is the sum of the lines ONLY — NO TAX IS
    COMPUTED by this tool (the tax rulings: the product ships a
    frozen-year, dated, comparison-only calculator, Indian law only).
  * payments — record / list. A payment is a RECORD against an
    invoice. Overpayment is refused with the numbers; a void invoice
    refuses payment. The invoice balance and status update in the
    same transaction as the payment.
  * employees — add / list / remove. A record only (name, role,
    start date). NO PAYROLL (see STOPs).
  * projects — add / list / get / set_status / remove. A record
    (name, customer, status, due date).
  * status — counts of every entity, open invoices (total + count),
    overdue invoices, low-stock products, ledger chain state.
  * events — the hash-chained ledger (list / verify).

Money is integer minor units (cents) everywhere, in the operator's
own currency — the tool never assumes a currency. Amounts are shown
as major units with two decimals (1,234.56 for 123456 cents).

What is NOT built (STOP, stated on the tin):
  * Payroll, tax computation, tax advice, tax filing. An employee
    record is a directory entry, not a pay run.
  * Money movement of any kind: no payment capture, no Stripe calls,
    no bank transfer, no live keys. Invoices and payments are records.
  * Multi-user roles, staff accounts, hosted anything, a web app,
    a public page, channel sync, or any network call. Single-user
    by design, local only.
  * Manufacturing (BOM, work orders), point of sale, recruitment,
    blogs, website, maintenance — the AureusERP plugin surface
    beyond the trading + CRM + HR-record + project core.
  * No subprocess, no network, no file reads of operator data beyond
    the arguments given. The operator supplies every number and name.

Bounds (refused honestly, with the numbers): at most 1,000
customers / leads / products / employees / projects; at most 10,000
sales orders / purchase orders / invoices / payments; at most 100
lines per order or invoice; each line quantity at most 100,000;
each amount at most 1,000,000,000 minor units; names at most 120
chars; list limit at most 100.
"""

import json
import re
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path

from .config import DATA_DIR
from . import store_base

PROPOSED_MARKER = "Parent-ruled (licence, 2026-11-12)"

_ACTIONS = ("customer", "lead", "product", "stock", "sales_order",
            "purchase_order", "invoice", "payment", "employee",
            "project", "status", "events")

_MAX_NAME = 120
_MAX_RECORDS = 1000
_MAX_ORDERS = 10000
_MAX_LINES = 100
_MAX_QTY = 100000
_MAX_CENTS = 1000000000
_MAX_LIMIT = 100
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_LOCK = threading.Lock()

_PREFIX = {"customer": "CUS", "lead": "LEAD", "product": "PRD",
           "sales_order": "SO", "purchase_order": "PO",
           "invoice": "INV", "payment": "PAY", "employee": "EMP",
           "project": "PRJ"}


class _Refusal(Exception):
    """An honest refusal: the handler returns 'refused: ...'."""


def _fmt_money(cents):
    """Integer minor units -> '1,234.56' (operator's own currency)."""
    sign = "-" if cents < 0 else ""
    n = abs(int(cents))
    return "%s%s.%02d" % (sign, f"{n // 100:,}", n % 100)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _clean_str(v, what, limit=_MAX_NAME, required=True):
    if v is None:
        if required:
            raise _Refusal("%s is required" % what)
        return None
    if not isinstance(v, str) or not v.strip():
        raise _Refusal("%s must be a non-empty string" % what)
    if len(v) > limit:
        raise _Refusal("%s is longer than %d characters"
                       % (what, limit))
    return v.strip()


def _clean_cents(v, what, minimum=0):
    if not _is_int(v):
        raise _Refusal("%s must be a whole number of minor units"
                       % what)
    if v < minimum or v > _MAX_CENTS:
        raise _Refusal("%s must be between %d and %d minor units"
                       % (what, minimum, _MAX_CENTS))
    return v


def _clean_qty(v, what, minimum=1):
    if not _is_int(v):
        raise _Refusal("%s must be a whole number" % what)
    if v < minimum or v > _MAX_QTY:
        raise _Refusal("%s must be between %d and %d"
                       % (what, minimum, _MAX_QTY))
    return v


def _clean_date(v, what):
    if v is None:
        return None
    if not isinstance(v, str) or not _DATE_RE.match(v):
        raise _Refusal("%s must be a YYYY-MM-DD date" % what)
    try:
        date(int(v[0:4]), int(v[5:7]), int(v[8:10]))
    except ValueError:
        raise _Refusal("%s is not a real calendar date" % what)
    return v


def _clean_limit(v):
    if v is None:
        return 20
    if not _is_int(v) or v < 1 or v > _MAX_LIMIT:
        raise _Refusal("limit must be between 1 and %d" % _MAX_LIMIT)
    return v


# ---------------------------------------------------------------------------
# Store: one SQLite file (DATA_DIR) — entities + a hash-chained ledger
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    return Path(DATA_DIR) / "erp.sqlite3"


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS customers ("
    " id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT,"
    " phone TEXT, company TEXT, created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS leads ("
    " id TEXT PRIMARY KEY, name TEXT NOT NULL, company TEXT,"
    " email TEXT, phone TEXT, status TEXT NOT NULL,"
    " converted_to TEXT, created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS products ("
    " id TEXT PRIMARY KEY, name TEXT NOT NULL,"
    " price_cents INTEGER NOT NULL, created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS stock_moves ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT NOT NULL,"
    " delta INTEGER NOT NULL, reason TEXT NOT NULL, ts REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sales_orders ("
    " id TEXT PRIMARY KEY, customer_id TEXT NOT NULL,"
    " status TEXT NOT NULL, total_cents INTEGER NOT NULL,"
    " created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sales_order_lines ("
    " order_id TEXT NOT NULL, line_no INTEGER NOT NULL,"
    " product_id TEXT NOT NULL, quantity INTEGER NOT NULL,"
    " price_cents INTEGER NOT NULL,"
    " PRIMARY KEY (order_id, line_no))",
    "CREATE TABLE IF NOT EXISTS purchase_orders ("
    " id TEXT PRIMARY KEY, vendor TEXT NOT NULL,"
    " status TEXT NOT NULL, total_cents INTEGER NOT NULL,"
    " created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS purchase_order_lines ("
    " order_id TEXT NOT NULL, line_no INTEGER NOT NULL,"
    " product_id TEXT NOT NULL, quantity INTEGER NOT NULL,"
    " price_cents INTEGER NOT NULL,"
    " PRIMARY KEY (order_id, line_no))",
    "CREATE TABLE IF NOT EXISTS invoices ("
    " id TEXT PRIMARY KEY, customer_id TEXT NOT NULL,"
    " order_id TEXT, total_cents INTEGER NOT NULL,"
    " paid_cents INTEGER NOT NULL, status TEXT NOT NULL,"
    " due_date TEXT, note TEXT, created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS payments ("
    " id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL,"
    " amount_cents INTEGER NOT NULL, note TEXT,"
    " created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS employees ("
    " id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT,"
    " start_date TEXT, created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS projects ("
    " id TEXT PRIMARY KEY, name TEXT NOT NULL, customer_id TEXT,"
    " status TEXT NOT NULL, due_date TEXT, created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS events ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
    " kind TEXT NOT NULL, payload TEXT NOT NULL,"
    " prev_hash TEXT NOT NULL, hash TEXT NOT NULL)",
)

_TABLES = {
    "customer": "customers", "lead": "leads", "product": "products",
    "sales_order": "sales_orders", "purchase_order": "purchase_orders",
    "invoice": "invoices", "payment": "payments",
    "employee": "employees", "project": "projects",
}


class _ErpStore(store_base.StoreBase):
    """Entities + a hash-chained ledger. Open-per-operation; every
    write is followed by a re-read before the caller claims it
    (A1-store). Reads answer empty WITHOUT creating the file."""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else _db_path()


    def _connect(self):
        conn = store_base.connect(
            str(self.path), pragmas=("PRAGMA journal_mode=WAL",))
        for stmt in _SCHEMA:
            conn.execute(stmt)
        conn.commit()
        return conn

    # -- ledger ---------------------------------------------------------

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
            (ts, kind, json.dumps(payload), prev_hash, digest))
        return digest

    def append_event(self, kind, payload):
        """Append a ledger event and re-read it (A1-store). Returns
        True when the re-read row matches what was written."""
        with _LOCK:
            conn = self._connect()
            try:
                digest = self._append_event_locked(conn, kind, payload)
                conn.commit()
                row = conn.execute(
                    "SELECT hash, payload FROM events"
                    " ORDER BY id DESC LIMIT 1").fetchone()
            finally:
                conn.close()
        if row is None or row[0] != digest:
            return False
        return json.loads(row[1]) == payload

    def list_events(self, limit=20):
        if self._missing():
            return []
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT id, ts, kind, payload FROM events"
                    " ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
            finally:
                conn.close()
        return rows

    def verify_chain(self):
        if self._missing():
            return True
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT ts, kind, payload, prev_hash, hash FROM"
                    " events ORDER BY id").fetchall()
            finally:
                conn.close()
        entries = [{"ts": ts, "kind": kind,
                    "payload": json.loads(payload),
                    "prev_hash": prev_hash, "hash": digest}
                   for ts, kind, payload, prev_hash, digest in rows]
        return store_base.verify_entries(
            entries, "json_body_ts", store_base.GENESIS)["ok"]

    def status_counts(self):
        if self._missing():
            return {}
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT kind, COUNT(*) FROM events"
                    " GROUP BY kind").fetchall()
            finally:
                conn.close()
        return {kind: count for kind, count in rows}

    # -- generic row layer (one connection per call; the caller holds
    #    _LOCK for multi-statement writes) ------------------------------

    def next_id(self, conn, entity):
        prefix = _PREFIX[entity]
        table = _TABLES[entity]
        row = conn.execute(
            "SELECT id FROM %s ORDER BY id DESC LIMIT 1"
            % table).fetchone()
        n = 0
        if row:
            try:
                n = int(row[0].split("-")[1])
            except (IndexError, ValueError):
                n = 0
        return "%s-%04d" % (prefix, n + 1)

    def count(self, conn, table, where="", args=()):
        row = conn.execute(
            "SELECT COUNT(*) FROM %s %s" % (table, where),
            args).fetchone()
        return row[0] if row else 0

    def get_row(self, conn, table, rid):
        return conn.execute(
            "SELECT * FROM %s WHERE id = ?" % table, (rid,)).fetchone()

    def stock_level(self, conn, product_id):
        row = conn.execute(
            "SELECT COALESCE(SUM(delta), 0) FROM stock_moves"
            " WHERE product_id = ?", (product_id,)).fetchone()
        return row[0] if row else 0


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

_EMPTY = "No business records yet — the ledger is empty."


def _last_event_payload(conn):
    row = conn.execute(
        "SELECT payload FROM events ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else None


def _do_customer(store, k, a):
    if k in ("list", "get", "remove") and store._missing():
        return _EMPTY
    if k == "add":
        name = _clean_str(a["name"], "name")
        email = _clean_str(a["email"], "email", required=False)
        phone = _clean_str(a["phone"], "phone", required=False)
        company = _clean_str(a["company"], "company", required=False)
        payload = {"action": "customer", "kind": "add", "name": name}
        with _LOCK:
            conn = store._connect()
            try:
                if store.count(conn, "customers") >= _MAX_RECORDS:
                    raise _Refusal("at most %d customers" % _MAX_RECORDS)
                rid = store.next_id(conn, "customer")
                conn.execute(
                    "INSERT INTO customers (id, name, email, phone,"
                    " company, created) VALUES (?, ?, ?, ?, ?, ?)",
                    (rid, name, email, phone, company, time.time()))
                payload["id"] = rid
                store._append_event_locked(conn, "customer.add", payload)
                conn.commit()
                row = store.get_row(conn, "customers", rid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[1] != name or ev != payload:
            return ("refused: the customer write did not re-read —"
                    " nothing was claimed")
        return "customer %s added: %s" % (rid, name)
    if k == "list":
        limit = _clean_limit(a["limit"])
        with _LOCK:
            conn = store._connect()
            try:
                rows = conn.execute(
                    "SELECT id, name, email, phone, company FROM"
                    " customers ORDER BY id LIMIT ?",
                    (limit,)).fetchall()
                total = store.count(conn, "customers")
            finally:
                conn.close()
        if not rows:
            return "No customers yet."
        lines = ["customers (%d):" % total]
        for rid, nm, em, ph, co in rows:
            extra = ", ".join(p for p in (em, ph, co) if p)
            lines.append("%s — %s%s" % (rid, nm,
                                        (" (" + extra + ")") if extra else ""))
        return "\n".join(lines)
    if k == "get":
        rid = _clean_str(a["id"], "id")
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "customers", rid)
                if row is None:
                    raise _Refusal("no customer with id %s" % rid)
                n_orders = store.count(conn, "sales_orders",
                                       "WHERE customer_id = ?", (rid,))
                n_inv = store.count(conn, "invoices",
                                    "WHERE customer_id = ?", (rid,))
                bal = conn.execute(
                    "SELECT COALESCE(SUM(total_cents - paid_cents), 0)"
                    " FROM invoices WHERE customer_id = ?"
                    " AND status = 'open'", (rid,)).fetchone()[0]
            finally:
                conn.close()
        _id, nm, em, ph, co, _ts = row
        out = ["%s — %s" % (_id, nm)]
        if em:
            out.append("email: %s" % em)
        if ph:
            out.append("phone: %s" % ph)
        if co:
            out.append("company: %s" % co)
        out.append("sales orders: %d, invoices: %d, open balance: %s"
                   % (n_orders, n_inv, _fmt_money(bal)))
        return "\n".join(out)
    if k == "remove":
        rid = _clean_str(a["id"], "id")
        payload = {"action": "customer", "kind": "remove", "id": rid}
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "customers", rid)
                if row is None:
                    raise _Refusal("no customer with id %s" % rid)
                n_orders = store.count(conn, "sales_orders",
                                       "WHERE customer_id = ?", (rid,))
                n_inv = store.count(conn, "invoices",
                                    "WHERE customer_id = ?", (rid,))
                if n_orders or n_inv:
                    raise _Refusal(
                        "customer %s has %d sales order(s) and %d"
                        " invoice(s) — remove those first"
                        % (rid, n_orders, n_inv))
                conn.execute("DELETE FROM customers WHERE id = ?",
                             (rid,))
                store._append_event_locked(conn, "customer.remove",
                                           payload)
                conn.commit()
                gone = store.get_row(conn, "customers", rid) is None
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if not gone or ev != payload:
            return ("refused: the customer removal did not re-read —"
                    " nothing was claimed")
        return "customer %s removed" % rid
    return ("refused: unknown kind %r for customer — use add / list /"
            " get / remove" % k)


def _do_lead(store, k, a):
    if k in ("list", "convert", "remove") and store._missing():
        return _EMPTY
    if k == "add":
        name = _clean_str(a["name"], "name")
        company = _clean_str(a["company"], "company", required=False)
        email = _clean_str(a["email"], "email", required=False)
        phone = _clean_str(a["phone"], "phone", required=False)
        payload = {"action": "lead", "kind": "add", "name": name}
        with _LOCK:
            conn = store._connect()
            try:
                if store.count(conn, "leads") >= _MAX_RECORDS:
                    raise _Refusal("at most %d leads" % _MAX_RECORDS)
                rid = store.next_id(conn, "lead")
                conn.execute(
                    "INSERT INTO leads (id, name, company, email,"
                    " phone, status, converted_to, created)"
                    " VALUES (?, ?, ?, ?, ?, 'open', NULL, ?)",
                    (rid, name, company, email, phone, time.time()))
                payload["id"] = rid
                store._append_event_locked(conn, "lead.add", payload)
                conn.commit()
                row = store.get_row(conn, "leads", rid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[1] != name or ev != payload:
            return ("refused: the lead write did not re-read —"
                    " nothing was claimed")
        return "lead %s added: %s" % (rid, name)
    if k == "list":
        limit = _clean_limit(a["limit"])
        with _LOCK:
            conn = store._connect()
            try:
                rows = conn.execute(
                    "SELECT id, name, company, status, converted_to"
                    " FROM leads ORDER BY id LIMIT ?",
                    (limit,)).fetchall()
                total = store.count(conn, "leads")
            finally:
                conn.close()
        if not rows:
            return "No leads yet."
        lines = ["leads (%d):" % total]
        for rid, nm, co, st, conv in rows:
            tail = " — converted to %s" % conv if conv else ""
            lines.append("%s — %s%s (%s)%s"
                         % (rid, nm, (" (" + co + ")") if co else "",
                            st, tail))
        return "\n".join(lines)
    if k == "convert":
        rid = _clean_str(a["id"], "id")
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "leads", rid)
                if row is None:
                    raise _Refusal("no lead with id %s" % rid)
                if row[5] == "converted":
                    raise _Refusal("lead %s was already converted to %s"
                                   % (rid, row[6]))
                if store.count(conn, "customers") >= _MAX_RECORDS:
                    raise _Refusal("at most %d customers" % _MAX_RECORDS)
                cid = store.next_id(conn, "customer")
                conn.execute(
                    "INSERT INTO customers (id, name, email, phone,"
                    " company, created) VALUES (?, ?, ?, ?, ?, ?)",
                    (cid, row[1], row[3], row[4], row[2], time.time()))
                conn.execute(
                    "UPDATE leads SET status = 'converted',"
                    " converted_to = ? WHERE id = ?", (cid, rid))
                payload = {"action": "lead", "kind": "convert",
                           "id": rid, "customer_id": cid}
                store._append_event_locked(conn, "lead.convert", payload)
                conn.commit()
                crow = store.get_row(conn, "customers", cid)
                lrow = store.get_row(conn, "leads", rid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if (crow is None or lrow is None or lrow[5] != "converted"
                or ev != payload):
            return ("refused: the lead conversion did not re-read —"
                    " nothing was claimed")
        return "lead %s converted to customer %s" % (rid, cid)
    if k == "remove":
        rid = _clean_str(a["id"], "id")
        payload = {"action": "lead", "kind": "remove", "id": rid}
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "leads", rid)
                if row is None:
                    raise _Refusal("no lead with id %s" % rid)
                conn.execute("DELETE FROM leads WHERE id = ?", (rid,))
                store._append_event_locked(conn, "lead.remove", payload)
                conn.commit()
                gone = store.get_row(conn, "leads", rid) is None
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if not gone or ev != payload:
            return ("refused: the lead removal did not re-read —"
                    " nothing was claimed")
        return "lead %s removed" % rid
    return ("refused: unknown kind %r for lead — use add / list /"
            " convert / remove" % k)


def _do_product(store, k, a):
    if k in ("list", "get", "remove") and store._missing():
        return _EMPTY
    if k == "add":
        name = _clean_str(a["name"], "name")
        price = _clean_cents(a["price_cents"], "price_cents")
        qty = _clean_qty(a["quantity"], "quantity", minimum=0) \
            if a["quantity"] is not None else 0
        payload = {"action": "product", "kind": "add", "name": name,
                   "price_cents": price, "quantity": qty}
        with _LOCK:
            conn = store._connect()
            try:
                if store.count(conn, "products") >= _MAX_RECORDS:
                    raise _Refusal("at most %d products" % _MAX_RECORDS)
                rid = store.next_id(conn, "product")
                conn.execute(
                    "INSERT INTO products (id, name, price_cents,"
                    " created) VALUES (?, ?, ?, ?)",
                    (rid, name, price, time.time()))
                if qty:
                    conn.execute(
                        "INSERT INTO stock_moves (product_id, delta,"
                        " reason, ts) VALUES (?, ?, 'initial stock', ?)",
                        (rid, qty, time.time()))
                store._append_event_locked(conn, "product.add", payload)
                conn.commit()
                row = store.get_row(conn, "products", rid)
                lvl = store.stock_level(conn, rid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[2] != price or lvl != qty \
                or ev != payload:
            return ("refused: the product write did not re-read —"
                    " nothing was claimed")
        return ("product %s added: %s — %s each, stock %d"
                % (rid, name, _fmt_money(price), qty))
    if k == "list":
        limit = _clean_limit(a["limit"])
        with _LOCK:
            conn = store._connect()
            try:
                rows = conn.execute(
                    "SELECT id, name, price_cents FROM products"
                    " ORDER BY id LIMIT ?", (limit,)).fetchall()
                total = store.count(conn, "products")
                levels = {r[0]: store.stock_level(conn, r[0])
                          for r in rows}
            finally:
                conn.close()
        if not rows:
            return "No products yet."
        lines = ["products (%d):" % total]
        for rid, nm, price in rows:
            lines.append("%s — %s — %s each — stock %d"
                         % (rid, nm, _fmt_money(price), levels[rid]))
        return "\n".join(lines)
    if k == "get":
        rid = _clean_str(a["id"], "id")
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "products", rid)
                if row is None:
                    raise _Refusal("no product with id %s" % rid)
                lvl = store.stock_level(conn, rid)
                n_moves = store.count(conn, "stock_moves",
                                      "WHERE product_id = ?", (rid,))
            finally:
                conn.close()
        return ("%s — %s\nprice: %s each\nstock: %d (%d movement(s))"
                % (row[0], row[1], _fmt_money(row[2]), lvl, n_moves))
    if k == "remove":
        rid = _clean_str(a["id"], "id")
        payload = {"action": "product", "kind": "remove", "id": rid}
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "products", rid)
                if row is None:
                    raise _Refusal("no product with id %s" % rid)
                n_so = store.count(conn, "sales_order_lines",
                                   "WHERE product_id = ?", (rid,))
                n_po = store.count(conn, "purchase_order_lines",
                                   "WHERE product_id = ?", (rid,))
                if n_so or n_po:
                    raise _Refusal(
                        "product %s is on %d sales order line(s) and"
                        " %d purchase order line(s) — it cannot be"
                        " removed" % (rid, n_so, n_po))
                conn.execute("DELETE FROM products WHERE id = ?",
                             (rid,))
                conn.execute("DELETE FROM stock_moves WHERE product_id"
                             " = ?", (rid,))
                store._append_event_locked(conn, "product.remove",
                                           payload)
                conn.commit()
                gone = store.get_row(conn, "products", rid) is None
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if not gone or ev != payload:
            return ("refused: the product removal did not re-read —"
                    " nothing was claimed")
        return "product %s removed" % rid
    return ("refused: unknown kind %r for product — use add / list /"
            " get / remove" % k)


def _do_stock(store, k, a):
    if store._missing():
        return _EMPTY
    if k == "adjust":
        pid = _clean_str(a["product_id"], "product_id")
        q = a["quantity"]
        if not _is_int(q) or q == 0 or abs(q) > _MAX_QTY:
            raise _Refusal("quantity must be a non-zero whole number"
                           " between -%d and %d"
                           % (_MAX_QTY, _MAX_QTY))
        delta = q
        reason = _clean_str(a["reason"], "reason")
        payload = {"action": "stock", "kind": "adjust",
                   "product_id": pid, "delta": delta, "reason": reason}
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "products", pid)
                if row is None:
                    raise _Refusal("no product with id %s" % pid)
                before = store.stock_level(conn, pid)
                if before + delta < 0:
                    raise _Refusal(
                        "stock for %s would go below zero: have %d,"
                        " adjustment %d" % (pid, before, delta))
                conn.execute(
                    "INSERT INTO stock_moves (product_id, delta,"
                    " reason, ts) VALUES (?, ?, ?, ?)",
                    (pid, delta, reason, time.time()))
                store._append_event_locked(conn, "stock.adjust", payload)
                conn.commit()
                after = store.stock_level(conn, pid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if after != before + delta or ev != payload:
            return ("refused: the stock adjustment did not re-read —"
                    " nothing was claimed")
        return ("stock for %s adjusted by %+d (%s) — now %d"
                % (pid, delta, reason, after))
    if k == "list":
        limit = _clean_limit(a["limit"])
        pid = a["product_id"]
        with _LOCK:
            conn = store._connect()
            try:
                if pid is not None:
                    pid = _clean_str(pid, "product_id")
                    row = store.get_row(conn, "products", pid)
                    if row is None:
                        raise _Refusal("no product with id %s" % pid)
                    lvl = store.stock_level(conn, pid)
                    moves = conn.execute(
                        "SELECT delta, reason FROM stock_moves"
                        " WHERE product_id = ? ORDER BY id DESC"
                        " LIMIT ?", (pid, limit)).fetchall()
                    return ("%s — %s — stock %d\nrecent movements:"
                            % (row[0], row[1], lvl)
                            + "".join("\n  %+d — %s" % (d, r)
                                     for d, r in moves)
                            if moves else
                            "%s — %s — stock %d\nno movements recorded"
                            % (row[0], row[1], lvl))
                rows = conn.execute(
                    "SELECT id, name FROM products ORDER BY id"
                    " LIMIT ?", (limit,)).fetchall()
                levels = {r[0]: store.stock_level(conn, r[0])
                          for r in rows}
            finally:
                conn.close()
        if not rows:
            return "No products yet."
        lines = ["stock:"]
        for rid, nm in rows:
            lines.append("%s — %s — %d" % (rid, nm, levels[rid]))
        return "\n".join(lines)
    return ("refused: unknown kind %r for stock — use adjust / list"
            % k)


def _parse_lines(lines, conn, require_price=False):
    """Validate order/invoice lines. Each line is
    {product_id, quantity, price_cents?}; the price defaults to the
    product's current price unless require_price. Returns
    [(product_id, quantity, price_cents, line_total)] and the total."""
    if not isinstance(lines, list) or not lines:
        raise _Refusal("lines must be a non-empty list")
    if len(lines) > _MAX_LINES:
        raise _Refusal("at most %d lines per order or invoice"
                       % _MAX_LINES)
    out = []
    total = 0
    for i, line in enumerate(lines):
        if not isinstance(line, dict):
            raise _Refusal("line %d must be an object" % (i + 1))
        pid = line.get("product_id")
        if not isinstance(pid, str) or not pid.strip():
            raise _Refusal("line %d needs a product_id" % (i + 1))
        pid = pid.strip()
        qty = _clean_qty(line.get("quantity"),
                         "line %d quantity" % (i + 1))
        prow = conn.execute(
            "SELECT price_cents FROM products WHERE id = ?",
            (pid,)).fetchone()
        if prow is None:
            raise _Refusal("line %d: no product with id %s"
                           % (i + 1, pid))
        if line.get("price_cents") is not None:
            price = _clean_cents(line.get("price_cents"),
                                 "line %d price_cents" % (i + 1))
        elif require_price:
            raise _Refusal("line %d needs a price_cents (the"
                           " supplier's price)" % (i + 1))
        else:
            price = prow[0]
        out.append((pid, qty, price, qty * price))
        total += qty * price
    return out, total


def _order_lines(conn, table, order_id):
    return conn.execute(
        "SELECT product_id, quantity, price_cents FROM %s"
        " WHERE order_id = ? ORDER BY line_no" % table,
        (order_id,)).fetchall()


def _do_sales_order(store, k, a):
    if k in ("list", "get", "fulfil", "cancel") and store._missing():
        return _EMPTY
    if k == "create":
        cid = _clean_str(a["customer_id"], "customer_id")
        with _LOCK:
            conn = store._connect()
            try:
                crow = store.get_row(conn, "customers", cid)
                if crow is None:
                    raise _Refusal("no customer with id %s" % cid)
                if store.count(conn, "sales_orders") >= _MAX_ORDERS:
                    raise _Refusal("at most %d sales orders"
                                   % _MAX_ORDERS)
                lines, total = _parse_lines(a["lines"], conn)
                rid = store.next_id(conn, "sales_order")
                conn.execute(
                    "INSERT INTO sales_orders (id, customer_id, status,"
                    " total_cents, created) VALUES (?, ?, 'open', ?, ?)",
                    (rid, cid, total, time.time()))
                for n, (pid, qty, price, _lt) in enumerate(lines, 1):
                    conn.execute(
                        "INSERT INTO sales_order_lines (order_id,"
                        " line_no, product_id, quantity, price_cents)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (rid, n, pid, qty, price))
                payload = {"action": "sales_order", "kind": "create",
                           "id": rid, "customer_id": cid,
                           "total_cents": total}
                store._append_event_locked(conn, "sales_order.create",
                                           payload)
                conn.commit()
                row = store.get_row(conn, "sales_orders", rid)
                n_lines = store.count(conn, "sales_order_lines",
                                      "WHERE order_id = ?", (rid,))
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[3] != total or n_lines != len(lines) \
                or ev != payload:
            return ("refused: the sales order write did not re-read —"
                    " nothing was claimed")
        return ("sales order %s created for %s: %d line(s), total %s"
                % (rid, cid, len(lines), _fmt_money(total)))
    if k == "list":
        limit = _clean_limit(a["limit"])
        cid = a["customer_id"]
        with _LOCK:
            conn = store._connect()
            try:
                if cid is not None:
                    cid = _clean_str(cid, "customer_id")
                    rows = conn.execute(
                        "SELECT id, customer_id, status, total_cents"
                        " FROM sales_orders WHERE customer_id = ?"
                        " ORDER BY id LIMIT ?", (cid, limit)).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, customer_id, status, total_cents"
                        " FROM sales_orders ORDER BY id LIMIT ?",
                        (limit,)).fetchall()
                total = store.count(conn, "sales_orders")
            finally:
                conn.close()
        if not rows:
            return "No sales orders yet."
        lines = ["sales orders (%d):" % total]
        for rid, c, st, tot in rows:
            lines.append("%s — %s — %s — %s" % (rid, c, st,
                                                _fmt_money(tot)))
        return "\n".join(lines)
    if k == "get":
        rid = _clean_str(a["id"], "id")
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "sales_orders", rid)
                if row is None:
                    raise _Refusal("no sales order with id %s" % rid)
                lines = _order_lines(conn, "sales_order_lines", rid)
            finally:
                conn.close()
        out = ["%s — customer %s — %s — total %s"
               % (row[0], row[1], row[2], _fmt_money(row[3]))]
        for pid, qty, price in lines:
            out.append("  %s x %d @ %s = %s"
                       % (pid, qty, _fmt_money(price),
                          _fmt_money(qty * price)))
        return "\n".join(out)
    if k == "fulfil":
        rid = _clean_str(a["id"], "id")
        payload = {"action": "sales_order", "kind": "fulfil", "id": rid}
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "sales_orders", rid)
                if row is None:
                    raise _Refusal("no sales order with id %s" % rid)
                if row[2] != "open":
                    raise _Refusal("sales order %s is %s — only an"
                                   " open order can be fulfilled"
                                   % (rid, row[2]))
                lines = _order_lines(conn, "sales_order_lines", rid)
                for pid, qty, _price in lines:
                    have = store.stock_level(conn, pid)
                    if have < qty:
                        raise _Refusal(
                            "cannot fulfil %s: product %s needs %d"
                            " but only %d in stock"
                            % (rid, pid, qty, have))
                for pid, qty, _price in lines:
                    conn.execute(
                        "INSERT INTO stock_moves (product_id, delta,"
                        " reason, ts) VALUES (?, ?, ?, ?)",
                        (pid, -qty,
                         "sales order %s fulfilment" % rid, time.time()))
                conn.execute(
                    "UPDATE sales_orders SET status = 'fulfilled'"
                    " WHERE id = ?", (rid,))
                store._append_event_locked(conn, "sales_order.fulfil",
                                           payload)
                conn.commit()
                row = store.get_row(conn, "sales_orders", rid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[2] != "fulfilled" or ev != payload:
            return ("refused: the fulfilment did not re-read —"
                    " nothing was claimed")
        return "sales order %s fulfilled — stock decremented" % rid
    if k == "cancel":
        rid = _clean_str(a["id"], "id")
        payload = {"action": "sales_order", "kind": "cancel", "id": rid}
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "sales_orders", rid)
                if row is None:
                    raise _Refusal("no sales order with id %s" % rid)
                if row[2] != "open":
                    raise _Refusal("sales order %s is %s — only an"
                                   " open order can be cancelled"
                                   % (rid, row[2]))
                conn.execute(
                    "UPDATE sales_orders SET status = 'cancelled'"
                    " WHERE id = ?", (rid,))
                store._append_event_locked(conn, "sales_order.cancel",
                                           payload)
                conn.commit()
                row = store.get_row(conn, "sales_orders", rid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[2] != "cancelled" or ev != payload:
            return ("refused: the cancellation did not re-read —"
                    " nothing was claimed")
        return "sales order %s cancelled" % rid
    return ("refused: unknown kind %r for sales_order — use create /"
            " list / get / fulfil / cancel" % k)


def _do_purchase_order(store, k, a):
    if k in ("list", "get", "receive", "cancel") and store._missing():
        return _EMPTY
    if k == "create":
        vendor = _clean_str(a["vendor"], "vendor")
        with _LOCK:
            conn = store._connect()
            try:
                if store.count(conn, "purchase_orders") >= _MAX_ORDERS:
                    raise _Refusal("at most %d purchase orders"
                                   % _MAX_ORDERS)
                lines, total = _parse_lines(a["lines"], conn,
                                            require_price=True)
                rid = store.next_id(conn, "purchase_order")
                conn.execute(
                    "INSERT INTO purchase_orders (id, vendor, status,"
                    " total_cents, created) VALUES (?, ?, 'open', ?, ?)",
                    (rid, vendor, total, time.time()))
                for n, (pid, qty, price, _lt) in enumerate(lines, 1):
                    conn.execute(
                        "INSERT INTO purchase_order_lines (order_id,"
                        " line_no, product_id, quantity, price_cents)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (rid, n, pid, qty, price))
                payload = {"action": "purchase_order", "kind": "create",
                           "id": rid, "vendor": vendor,
                           "total_cents": total}
                store._append_event_locked(conn, "purchase_order.create",
                                           payload)
                conn.commit()
                row = store.get_row(conn, "purchase_orders", rid)
                n_lines = store.count(conn, "purchase_order_lines",
                                      "WHERE order_id = ?", (rid,))
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[3] != total or n_lines != len(lines) \
                or ev != payload:
            return ("refused: the purchase order write did not"
                    " re-read — nothing was claimed")
        return ("purchase order %s created from %s: %d line(s), total"
                " %s" % (rid, vendor, len(lines), _fmt_money(total)))
    if k == "list":
        limit = _clean_limit(a["limit"])
        with _LOCK:
            conn = store._connect()
            try:
                rows = conn.execute(
                    "SELECT id, vendor, status, total_cents FROM"
                    " purchase_orders ORDER BY id LIMIT ?",
                    (limit,)).fetchall()
                total = store.count(conn, "purchase_orders")
            finally:
                conn.close()
        if not rows:
            return "No purchase orders yet."
        lines = ["purchase orders (%d):" % total]
        for rid, ven, st, tot in rows:
            lines.append("%s — %s — %s — %s" % (rid, ven, st,
                                                _fmt_money(tot)))
        return "\n".join(lines)
    if k == "get":
        rid = _clean_str(a["id"], "id")
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "purchase_orders", rid)
                if row is None:
                    raise _Refusal("no purchase order with id %s" % rid)
                lines = _order_lines(conn, "purchase_order_lines", rid)
            finally:
                conn.close()
        out = ["%s — vendor %s — %s — total %s"
               % (row[0], row[1], row[2], _fmt_money(row[3]))]
        for pid, qty, price in lines:
            out.append("  %s x %d @ %s = %s"
                       % (pid, qty, _fmt_money(price),
                          _fmt_money(qty * price)))
        return "\n".join(out)
    if k == "receive":
        rid = _clean_str(a["id"], "id")
        payload = {"action": "purchase_order", "kind": "receive",
                   "id": rid}
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "purchase_orders", rid)
                if row is None:
                    raise _Refusal("no purchase order with id %s" % rid)
                if row[2] != "open":
                    raise _Refusal("purchase order %s is %s — only an"
                                   " open order can be received"
                                   % (rid, row[2]))
                lines = _order_lines(conn, "purchase_order_lines", rid)
                for pid, qty, _price in lines:
                    conn.execute(
                        "INSERT INTO stock_moves (product_id, delta,"
                        " reason, ts) VALUES (?, ?, ?, ?)",
                        (pid, qty,
                         "purchase order %s receipt" % rid, time.time()))
                conn.execute(
                    "UPDATE purchase_orders SET status = 'received'"
                    " WHERE id = ?", (rid,))
                store._append_event_locked(conn, "purchase_order.receive",
                                           payload)
                conn.commit()
                row = store.get_row(conn, "purchase_orders", rid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[2] != "received" or ev != payload:
            return ("refused: the receipt did not re-read — nothing"
                    " was claimed")
        return "purchase order %s received — stock incremented" % rid
    if k == "cancel":
        rid = _clean_str(a["id"], "id")
        payload = {"action": "purchase_order", "kind": "cancel",
                   "id": rid}
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "purchase_orders", rid)
                if row is None:
                    raise _Refusal("no purchase order with id %s" % rid)
                if row[2] != "open":
                    raise _Refusal("purchase order %s is %s — only an"
                                   " open order can be cancelled"
                                   % (rid, row[2]))
                conn.execute(
                    "UPDATE purchase_orders SET status = 'cancelled'"
                    " WHERE id = ?", (rid,))
                store._append_event_locked(conn, "purchase_order.cancel",
                                           payload)
                conn.commit()
                row = store.get_row(conn, "purchase_orders", rid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[2] != "cancelled" or ev != payload:
            return ("refused: the cancellation did not re-read —"
                    " nothing was claimed")
        return "purchase order %s cancelled" % rid
    return ("refused: unknown kind %r for purchase_order — use create /"
            " list / get / receive / cancel" % k)


def _do_invoice(store, k, a):
    if k in ("list", "get", "void") and store._missing():
        return _EMPTY
    if k == "create":
        due = _clean_date(a["due_date"], "due_date")
        note = _clean_str(a["note"], "note", required=False)
        with _LOCK:
            conn = store._connect()
            try:
                if store.count(conn, "invoices") >= _MAX_ORDERS:
                    raise _Refusal("at most %d invoices" % _MAX_ORDERS)
                order_id = a["order_id"]
                if order_id is not None:
                    order_id = _clean_str(order_id, "order_id")
                    orow = store.get_row(conn, "sales_orders", order_id)
                    if orow is None:
                        raise _Refusal("no sales order with id %s"
                                       % order_id)
                    if orow[2] == "cancelled":
                        raise _Refusal("sales order %s is cancelled —"
                                       " it cannot be invoiced"
                                       % order_id)
                    n_inv = store.count(conn, "invoices",
                                        "WHERE order_id = ?",
                                        (order_id,))
                    if n_inv:
                        raise _Refusal(
                            "sales order %s already has an invoice"
                            " — one invoice per order" % order_id)
                    cid = orow[1]
                    total = orow[3]
                    # the lines and total come from the order (frozen);
                    # the invoice itself stores no separate lines.
                else:
                    cid = _clean_str(a["customer_id"], "customer_id")
                    if store.get_row(conn, "customers", cid) is None:
                        raise _Refusal("no customer with id %s" % cid)
                    lines, total = _parse_lines(a["lines"], conn)
                rid = store.next_id(conn, "invoice")
                conn.execute(
                    "INSERT INTO invoices (id, customer_id, order_id,"
                    " total_cents, paid_cents, status, due_date, note,"
                    " created) VALUES (?, ?, ?, ?, 0, 'open', ?, ?, ?)",
                    (rid, cid, order_id, total, due, note, time.time()))
                payload = {"action": "invoice", "kind": "create",
                           "id": rid, "customer_id": cid,
                           "order_id": order_id, "total_cents": total}
                store._append_event_locked(conn, "invoice.create",
                                           payload)
                conn.commit()
                row = store.get_row(conn, "invoices", rid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[3] != total or ev != payload:
            return ("refused: the invoice write did not re-read —"
                    " nothing was claimed")
        return ("invoice %s created for %s: total %s (no tax computed"
                " — this tool never computes tax)"
                % (rid, cid, _fmt_money(total)))
    if k == "list":
        limit = _clean_limit(a["limit"])
        cid = a["customer_id"]
        with _LOCK:
            conn = store._connect()
            try:
                if cid is not None:
                    cid = _clean_str(cid, "customer_id")
                    rows = conn.execute(
                        "SELECT id, customer_id, total_cents,"
                        " paid_cents, status, due_date FROM invoices"
                        " WHERE customer_id = ? ORDER BY id LIMIT ?",
                        (cid, limit)).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, customer_id, total_cents,"
                        " paid_cents, status, due_date FROM invoices"
                        " ORDER BY id LIMIT ?", (limit,)).fetchall()
                total = store.count(conn, "invoices")
            finally:
                conn.close()
        if not rows:
            return "No invoices yet."
        lines = ["invoices (%d):" % total]
        for rid, c, tot, paid, st, due in rows:
            lines.append("%s — %s — total %s, paid %s, balance %s"
                         " — %s%s"
                         % (rid, c, _fmt_money(tot), _fmt_money(paid),
                            _fmt_money(tot - paid), st,
                            (" (due " + due + ")") if due else ""))
        return "\n".join(lines)
    if k == "get":
        rid = _clean_str(a["id"], "id")
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "invoices", rid)
                if row is None:
                    raise _Refusal("no invoice with id %s" % rid)
                pays = conn.execute(
                    "SELECT id, amount_cents, note FROM payments"
                    " WHERE invoice_id = ? ORDER BY id",
                    (rid,)).fetchall()
            finally:
                conn.close()
        out = ["%s — customer %s — total %s, paid %s, balance %s — %s"
               % (row[0], row[1], _fmt_money(row[3]),
                  _fmt_money(row[4]), _fmt_money(row[3] - row[4]),
                  row[5])]
        if row[2]:
            out.append("sales order: %s" % row[2])
        if row[6]:
            out.append("due: %s" % row[6])
        if row[7]:
            out.append("note: %s" % row[7])
        out.append("no tax computed — this tool never computes tax")
        for pid, amt, nt in pays:
            out.append("  payment %s — %s%s"
                       % (pid, _fmt_money(amt),
                          (" (" + nt + ")") if nt else ""))
        return "\n".join(out)
    if k == "void":
        rid = _clean_str(a["id"], "id")
        payload = {"action": "invoice", "kind": "void", "id": rid}
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "invoices", rid)
                if row is None:
                    raise _Refusal("no invoice with id %s" % rid)
                if row[5] == "void":
                    raise _Refusal("invoice %s is already void" % rid)
                if row[4] > 0:
                    raise _Refusal(
                        "invoice %s already has %s recorded in"
                        " payments — it cannot be voided"
                        % (rid, _fmt_money(row[4])))
                conn.execute(
                    "UPDATE invoices SET status = 'void' WHERE id = ?",
                    (rid,))
                store._append_event_locked(conn, "invoice.void", payload)
                conn.commit()
                row = store.get_row(conn, "invoices", rid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[5] != "void" or ev != payload:
            return ("refused: the void did not re-read — nothing was"
                    " claimed")
        return "invoice %s voided" % rid
    return ("refused: unknown kind %r for invoice — use create / list /"
            " get / void" % k)


def _do_payment(store, k, a):
    if k in ("list",) and store._missing():
        return _EMPTY
    if k == "record":
        iid = _clean_str(a["invoice_id"], "invoice_id")
        amount = _clean_cents(a["amount_cents"], "amount_cents",
                              minimum=1)
        note = _clean_str(a["note"], "note", required=False)
        payload = {"action": "payment", "kind": "record",
                   "invoice_id": iid, "amount_cents": amount}
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "invoices", iid)
                if row is None:
                    raise _Refusal("no invoice with id %s" % iid)
                if row[5] == "void":
                    raise _Refusal("invoice %s is void — it cannot"
                                   " take a payment" % iid)
                if store.count(conn, "payments") >= _MAX_ORDERS:
                    raise _Refusal("at most %d payments" % _MAX_ORDERS)
                remaining = row[3] - row[4]
                if amount > remaining:
                    raise _Refusal(
                        "payment of %s would overpay invoice %s:"
                        " total %s, already paid %s, remaining %s"
                        % (_fmt_money(amount), iid, _fmt_money(row[3]),
                           _fmt_money(row[4]), _fmt_money(remaining)))
                rid = store.next_id(conn, "payment")
                conn.execute(
                    "INSERT INTO payments (id, invoice_id,"
                    " amount_cents, note, created)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (rid, iid, amount, note, time.time()))
                new_paid = row[4] + amount
                new_status = "paid" if new_paid >= row[3] else "open"
                conn.execute(
                    "UPDATE invoices SET paid_cents = ?, status = ?"
                    " WHERE id = ?", (new_paid, new_status, iid))
                payload["id"] = rid
                store._append_event_locked(conn, "payment.record",
                                           payload)
                conn.commit()
                prow = store.get_row(conn, "payments", rid)
                irow = store.get_row(conn, "invoices", iid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if (prow is None or prow[2] != amount or irow is None
                or irow[4] != new_paid or ev != payload):
            return ("refused: the payment write did not re-read —"
                    " nothing was claimed")
        return ("payment %s recorded against invoice %s: %s — invoice"
                " balance now %s (%s)"
                % (rid, iid, _fmt_money(amount),
                   _fmt_money(irow[3] - irow[4]), irow[5]))
    if k == "list":
        limit = _clean_limit(a["limit"])
        iid = a["invoice_id"]
        with _LOCK:
            conn = store._connect()
            try:
                if iid is not None:
                    iid = _clean_str(iid, "invoice_id")
                    rows = conn.execute(
                        "SELECT id, invoice_id, amount_cents, note"
                        " FROM payments WHERE invoice_id = ?"
                        " ORDER BY id LIMIT ?", (iid, limit)).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, invoice_id, amount_cents, note"
                        " FROM payments ORDER BY id LIMIT ?",
                        (limit,)).fetchall()
                total = store.count(conn, "payments")
            finally:
                conn.close()
        if not rows:
            return "No payments yet."
        lines = ["payments (%d):" % total]
        for rid, inv, amt, nt in rows:
            lines.append("%s — invoice %s — %s%s"
                         % (rid, inv, _fmt_money(amt),
                            (" (" + nt + ")") if nt else ""))
        return "\n".join(lines)
    return ("refused: unknown kind %r for payment — use record / list"
            % k)


_PROJECT_STATUSES = ("active", "on_hold", "done")
_LOW_STOCK = 5


def _do_employee(store, k, a):
    if k in ("list", "get", "remove") and store._missing():
        return _EMPTY
    if k == "add":
        name = _clean_str(a["name"], "name")
        role = _clean_str(a["role"], "role", required=False)
        start = _clean_date(a["start_date"], "start_date")
        payload = {"action": "employee", "kind": "add", "name": name}
        with _LOCK:
            conn = store._connect()
            try:
                if store.count(conn, "employees") >= _MAX_RECORDS:
                    raise _Refusal("at most %d employees" % _MAX_RECORDS)
                rid = store.next_id(conn, "employee")
                conn.execute(
                    "INSERT INTO employees (id, name, role,"
                    " start_date, created) VALUES (?, ?, ?, ?, ?)",
                    (rid, name, role, start, time.time()))
                payload["id"] = rid
                store._append_event_locked(conn, "employee.add", payload)
                conn.commit()
                row = store.get_row(conn, "employees", rid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[1] != name or ev != payload:
            return ("refused: the employee write did not re-read —"
                    " nothing was claimed")
        return "employee %s added: %s%s" % (
            rid, name, (" (%s)" % role) if role else "")
    if k == "list":
        limit = _clean_limit(a["limit"])
        with _LOCK:
            conn = store._connect()
            try:
                rows = conn.execute(
                    "SELECT id, name, role, start_date FROM employees"
                    " ORDER BY id LIMIT ?", (limit,)).fetchall()
                total = store.count(conn, "employees")
            finally:
                conn.close()
        if not rows:
            return "No employees yet."
        lines = ["employees (%d):" % total]
        for rid, nm, rl, st in rows:
            lines.append("%s — %s%s%s"
                         % (rid, nm, (" (" + rl + ")") if rl else "",
                            (" — since " + st) if st else ""))
        return "\n".join(lines)
    if k == "get":
        rid = _clean_str(a["id"], "id")
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "employees", rid)
                if row is None:
                    raise _Refusal("no employee with id %s" % rid)
            finally:
                conn.close()
        out = ["%s — %s" % (row[0], row[1])]
        if row[2]:
            out.append("role: %s" % row[2])
        if row[3]:
            out.append("start date: %s" % row[3])
        out.append("No payroll is kept by this tool.")
        return "\n".join(out)
    if k == "remove":
        rid = _clean_str(a["id"], "id")
        payload = {"action": "employee", "kind": "remove", "id": rid}
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "employees", rid)
                if row is None:
                    raise _Refusal("no employee with id %s" % rid)
                conn.execute("DELETE FROM employees WHERE id = ?",
                             (rid,))
                store._append_event_locked(conn, "employee.remove",
                                           payload)
                conn.commit()
                gone = store.get_row(conn, "employees", rid) is None
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if not gone or ev != payload:
            return ("refused: the employee removal did not re-read —"
                    " nothing was claimed")
        return "employee %s removed" % rid
    return ("refused: unknown kind %r for employee — use add / list /"
            " get / remove" % k)


def _do_project(store, k, a):
    if k in ("list", "get", "set_status", "remove") \
            and store._missing():
        return _EMPTY
    if k == "add":
        name = _clean_str(a["name"], "name")
        cid = a["customer_id"]
        st = a["status"]
        if st is None:
            st = "active"
        if st not in _PROJECT_STATUSES:
            raise _Refusal("status must be one of active / on_hold /"
                           " done")
        due = _clean_date(a["due_date"], "due_date")
        payload = {"action": "project", "kind": "add", "name": name}
        with _LOCK:
            conn = store._connect()
            try:
                if cid is not None:
                    cid = _clean_str(cid, "customer_id")
                    if store.get_row(conn, "customers", cid) is None:
                        raise _Refusal("no customer with id %s" % cid)
                if store.count(conn, "projects") >= _MAX_RECORDS:
                    raise _Refusal("at most %d projects" % _MAX_RECORDS)
                rid = store.next_id(conn, "project")
                conn.execute(
                    "INSERT INTO projects (id, name, customer_id,"
                    " status, due_date, created)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (rid, name, cid, st, due, time.time()))
                payload["id"] = rid
                store._append_event_locked(conn, "project.add", payload)
                conn.commit()
                row = store.get_row(conn, "projects", rid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[1] != name or ev != payload:
            return ("refused: the project write did not re-read —"
                    " nothing was claimed")
        return "project %s added: %s (%s)" % (rid, name, st)
    if k == "list":
        limit = _clean_limit(a["limit"])
        st = a["status"]
        with _LOCK:
            conn = store._connect()
            try:
                if st is not None:
                    if st not in _PROJECT_STATUSES:
                        raise _Refusal(
                            "status must be one of active / on_hold /"
                            " done")
                    rows = conn.execute(
                        "SELECT id, name, customer_id, status, due_date"
                        " FROM projects WHERE status = ? ORDER BY id"
                        " LIMIT ?", (st, limit)).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, name, customer_id, status, due_date"
                        " FROM projects ORDER BY id LIMIT ?",
                        (limit,)).fetchall()
                total = store.count(conn, "projects")
            finally:
                conn.close()
        if not rows:
            return "No projects yet."
        lines = ["projects (%d):" % total]
        for rid, nm, cid, s, due in rows:
            lines.append("%s — %s — %s%s%s"
                         % (rid, nm, s,
                            (" (customer " + cid + ")") if cid else "",
                            (" (due " + due + ")") if due else ""))
        return "\n".join(lines)
    if k == "get":
        rid = _clean_str(a["id"], "id")
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "projects", rid)
                if row is None:
                    raise _Refusal("no project with id %s" % rid)
            finally:
                conn.close()
        out = ["%s — %s — %s" % (row[0], row[1], row[3])]
        if row[2]:
            out.append("customer: %s" % row[2])
        if row[4]:
            out.append("due: %s" % row[4])
        return "\n".join(out)
    if k == "set_status":
        rid = _clean_str(a["id"], "id")
        st = a["status"]
        if st not in _PROJECT_STATUSES:
            raise _Refusal("status must be one of active / on_hold /"
                           " done")
        payload = {"action": "project", "kind": "set_status",
                   "id": rid, "status": st}
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "projects", rid)
                if row is None:
                    raise _Refusal("no project with id %s" % rid)
                conn.execute(
                    "UPDATE projects SET status = ? WHERE id = ?",
                    (st, rid))
                store._append_event_locked(conn, "project.set_status",
                                           payload)
                conn.commit()
                row = store.get_row(conn, "projects", rid)
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[3] != st or ev != payload:
            return ("refused: the status change did not re-read —"
                    " nothing was claimed")
        return "project %s is now %s" % (rid, st)
    if k == "remove":
        rid = _clean_str(a["id"], "id")
        payload = {"action": "project", "kind": "remove", "id": rid}
        with _LOCK:
            conn = store._connect()
            try:
                row = store.get_row(conn, "projects", rid)
                if row is None:
                    raise _Refusal("no project with id %s" % rid)
                conn.execute("DELETE FROM projects WHERE id = ?",
                             (rid,))
                store._append_event_locked(conn, "project.remove",
                                           payload)
                conn.commit()
                gone = store.get_row(conn, "projects", rid) is None
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if not gone or ev != payload:
            return ("refused: the project removal did not re-read —"
                    " nothing was claimed")
        return "project %s removed" % rid
    return ("refused: unknown kind %r for project — use add / list /"
            " get / set_status / remove" % k)


def _do_status(store, a):
    if store._missing():
        return _EMPTY
    with _LOCK:
        conn = store._connect()
        try:
            counts = {e: store.count(conn, t)
                      for e, t in _TABLES.items()}
            open_inv = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(total_cents"
                " - paid_cents), 0) FROM invoices"
                " WHERE status = 'open'").fetchone()
            paid_inv = store.count(conn, "invoices",
                                   "WHERE status = 'paid'")
            void_inv = store.count(conn, "invoices",
                                   "WHERE status = 'void'")
            today = date.today().isoformat()
            overdue = conn.execute(
                "SELECT COUNT(*) FROM invoices WHERE status = 'open'"
                " AND due_date IS NOT NULL AND due_date < ?",
                (today,)).fetchone()[0]
            prows = conn.execute(
                "SELECT id, name FROM products ORDER BY id").fetchall()
            low = [(r[0], r[1], store.stock_level(conn, r[0]))
                   for r in prows]
            low = [t for t in low if t[2] <= _LOW_STOCK]
        finally:
            conn.close()
    chain_ok = store.verify_chain()
    ev_total = sum(store.status_counts().values())
    lines = [
        "business record:",
        "customers: %d, leads: %d, products: %d, employees: %d,"
        " projects: %d" % (counts["customer"], counts["lead"],
                           counts["product"], counts["employee"],
                           counts["project"]),
        "sales orders: %d, purchase orders: %d" % (
            counts["sales_order"], counts["purchase_order"]),
        "invoices: %d open (%s outstanding), %d paid, %d void"
        % (open_inv[0], _fmt_money(open_inv[1]), paid_inv, void_inv),
        "overdue invoices: %d" % overdue,
        "payments recorded: %d" % counts["payment"],
        "ledger events: %d, chain %s"
        % (ev_total, "verifies" if chain_ok else "does NOT verify"),
    ]
    if low:
        lines.append("low stock (at most %d):" % _LOW_STOCK)
        for rid, nm, lvl in low[:5]:
            lines.append("  %s — %s — %d" % (rid, nm, lvl))
    return "\n".join(lines)


def _do_events(store, k, a):
    if store._missing():
        return _EMPTY
    if k == "verify":
        ok = store.verify_chain()
        total = sum(store.status_counts().values())
        if ok:
            return ("the ledger chain verifies over %d event(s)"
                    % total)
        return ("the ledger chain does NOT verify over %d event(s) —"
                " the record is not trustworthy until the cause is"
                " found" % total)
    if k == "list":
        limit = _clean_limit(a["limit"])
        rows = store.list_events(limit=limit)
        if not rows:
            return "No ledger events yet."
        lines = ["ledger events (most recent first):"]
        for eid, _ts, kind, payload in rows:
            lines.append("#%d %s — %s" % (eid, kind, payload))
        return "\n".join(lines)
    return "refused: unknown kind %r for events — use list / verify" % k


def erp(action=None, kind=None, id=None, name=None, email=None,
        phone=None, company=None, role=None, start_date=None,
        status=None, product_id=None, price_cents=None, quantity=None,
        reason=None, lines=None, customer_id=None, order_id=None,
        invoice_id=None, amount_cents=None, due_date=None, vendor=None,
        note=None, limit=None, _store=None):
    """Keep one local business record: customers, leads, products,
    stock, sales/purchase orders, invoices, payments, employees and
    projects, tied together by a hash-chained ledger. Invoices and
    payments are records the owner made — the money moved outside
    VYUK, this tool never takes payment, never computes tax, and
    never keeps payroll. Returns a plain-text answer."""
    store = _store if _store is not None else _ErpStore()
    act = str(action or "").strip().lower()
    if act not in _ACTIONS:
        return ("refused: unknown action %r — use customer / lead /"
                " product / stock / sales_order / purchase_order /"
                " invoice / payment / employee / project / status /"
                " events" % act)
    k = str(kind or "").strip().lower() or "list"
    a = {"action": act, "kind": k, "id": id, "name": name,
         "email": email, "phone": phone, "company": company,
         "role": role, "start_date": start_date, "status": status,
         "product_id": product_id, "price_cents": price_cents,
         "quantity": quantity, "reason": reason, "lines": lines,
         "customer_id": customer_id, "order_id": order_id,
         "invoice_id": invoice_id, "amount_cents": amount_cents,
         "due_date": due_date, "vendor": vendor, "note": note,
         "limit": limit}
    try:
        if act == "customer":
            return _do_customer(store, k, a)
        if act == "lead":
            return _do_lead(store, k, a)
        if act == "product":
            return _do_product(store, k, a)
        if act == "stock":
            return _do_stock(store, k, a)
        if act == "sales_order":
            return _do_sales_order(store, k, a)
        if act == "purchase_order":
            return _do_purchase_order(store, k, a)
        if act == "invoice":
            return _do_invoice(store, k, a)
        if act == "payment":
            return _do_payment(store, k, a)
        if act == "employee":
            return _do_employee(store, k, a)
        if act == "project":
            return _do_project(store, k, a)
        if act == "status":
            return _do_status(store, a)
        return _do_events(store, k, a)
    except _Refusal as exc:
        return "refused: %s" % exc


def register_all(registry) -> None:
    """Register the ERP pack (R19-3, aureuserp/aureuserp
    inspiration): erp — a unified local business record in own
    stdlib code (customers, leads, products, stock derived from the
    movement ledger, sales/purchase orders, invoices, payments,
    employees, projects) over a hash-chained ledger. Free by design
    (app-state): every record is the owner's own local data, and
    invoices/payments are records the owner made — the money moved
    outside VYUK, the tool never takes payment, never computes tax,
    never keeps payroll. Nothing adopted — no PHP code, no Laravel,
    no Filament, no MySQL, no wrapper, no port. The module owns its
    catalogue via register_all (same pattern as the other packs)."""
    registry.register(
        name="erp",
        description=(
            "Keep one local business record: customers, leads, "
            "products, stock, sales and purchase orders, invoices, "
            "payments, employees and projects, tied together by a "
            "hash-chained ledger. A sales order reserves nothing "
            "until fulfilled (fulfilment decrements stock and is "
            "refused when any line is short); a purchase order "
            "receipt increments stock; an invoice is a record the "
            "owner made (the money moved outside VYUK — this tool "
            "never takes payment and never computes tax); a payment "
            "is a record against an invoice and overpayment is "
            "refused. Amounts are integer minor units in the "
            "operator's own currency. No payroll, no tax computation, "
            "no multi-user roles, no hosted anything, no network. "
            "Use when the user wants to record a customer, a sale, "
            "a purchase, an invoice or a payment, check stock, or "
            "see what the business owes and is owed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "One of: 'customer', 'lead', 'product', 'stock', 'sales_order', 'purchase_order', 'invoice', 'payment', 'employee', 'project', 'status' or 'events'"},
                "kind": {"type": "string",
                         "description": "The sub-action: 'add', 'list', 'get' or 'remove' for customer/product/employee; 'add', 'list', 'convert' or 'remove' for lead; 'adjust' or 'list' for stock; 'create', 'list', 'get', 'fulfil' or 'cancel' for sales_order; 'create', 'list', 'get', 'receive' or 'cancel' for purchase_order; 'create', 'list', 'get' or 'void' for invoice; 'record' or 'list' for payment; 'add', 'list', 'get', 'set_status' or 'remove' for project; 'list' or 'verify' for events (list if left out)"},
                "id": {"type": "string",
                       "description": "The record id (CUS-0001, PRD-0001, SO-0001, PO-0001, INV-0001, PAY-0001, EMP-0001, PRJ-0001, LEAD-0001) for get / remove / convert / fulfil / cancel / receive / void / set_status"},
                "name": {"type": "string",
                         "description": "With add, the name of the customer, lead, product, employee or project (at most 120 chars)"},
                "email": {"type": "string",
                          "description": "With customer / lead / employee add, the email address"},
                "phone": {"type": "string",
                          "description": "With customer / lead add, the phone number"},
                "company": {"type": "string",
                            "description": "With customer / lead add, the company name"},
                "role": {"type": "string",
                         "description": "With employee add, the role (for example 'manager', 'clerk')"},
                "start_date": {"type": "string",
                               "description": "With employee add, the start date as YYYY-MM-DD"},
                "status": {"type": "string",
                           "description": "With project add / set_status, one of 'active', 'on_hold', 'done'; with project list, a status to filter by"},
                "product_id": {"type": "string",
                               "description": "With stock adjust / list, the product id (PRD-0001)"},
                "price_cents": {"type": "integer",
                                "description": "With product add, the unit price in minor units (whole number, at least 0)"},
                "quantity": {"type": "integer",
                             "description": "With product add, the initial stock (0 if left out); with stock adjust, the signed change (non-zero, negative removes stock)"},
                "reason": {"type": "string",
                           "description": "With stock adjust, the reason for the correction (for example 'annual count')"},
                "lines": {"type": "array",
                          "description": "With sales_order / purchase_order / invoice create, the lines as [{product_id, quantity, price_cents}] — price_cents defaults to the product's current price, except on a purchase order where the supplier's price is required; at most 100 lines"},
                "customer_id": {"type": "string",
                                "description": "With sales_order / invoice create, the customer id; with project add, the customer the project is for"},
                "order_id": {"type": "string",
                             "description": "With invoice create, the sales order id to invoice (one invoice per order; the lines and total come from the order)"},
                "invoice_id": {"type": "string",
                               "description": "With payment record / list, the invoice id"},
                "amount_cents": {"type": "integer",
                                 "description": "With payment record, the amount in minor units (whole number, at least 1, at most the invoice balance)"},
                "due_date": {"type": "string",
                             "description": "With invoice create, the due date as YYYY-MM-DD; with project add, the due date"},
                "vendor": {"type": "string",
                           "description": "With purchase_order create, the supplier name"},
                "note": {"type": "string",
                         "description": "With invoice create / payment record, a short note"},
                "limit": {"type": "integer",
                          "description": "With list / events, most rows shown (20 if left out, max 100)"},
            },
            "required": ["action"],
        },
        handler=erp,
    )
