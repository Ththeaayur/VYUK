"""Deal pipeline and support tickets on erp customers (R19-8,
Django-CRM/Django-CRM inspiration).

BottleCRM (Django-CRM) is a self-hosted CRM built with Django REST
Framework, SvelteKit and Flutter: leads, accounts, contacts,
opportunities, support tickets, tasks and invoices, with
multi-tenant PostgreSQL Row-Level Security. This module builds the
honest buildable remainder in own stdlib code: the two pieces the
tree does NOT have — a DEAL PIPELINE and SUPPORT TICKETS — on top
of erp's customer records (R19-3, which already keeps customers,
leads with lead-to-customer conversion, products, orders, invoices,
payments, employees and projects). It does NOT rebuild customer,
lead or invoice records: a deal or a ticket names an erp customer
by id (CUS-0001) or by unique name, and the erp store is read
read-only to confirm the customer exists.

Licence: MIT — "Parent-ruled (licence, 2026-11-12)" (the Django-CRM LICENSE,
read in full from master: 1,101 bytes, 22 lines, copyright
"Copyright (c) 2017 MicroPyramid / Copyright (c) 2026 RunCode Inc.").
Nothing is adopted — no Django, no DRF, no SvelteKit, no Flutter,
no PostgreSQL, no web app, no wrapper, no port. The build takes
only the CRM shapes (deal pipeline with seeded stages and stage
kinds, opportunity with probability and aging, support ticket with
priority and SLA targets) and rebuilds them as own stdlib code.

What is built (one effectful tool `deals`, free by design):
  * pipeline — add / list / get / remove / set_default. A pipeline
    is a named sequence of deal stages; exactly one is the default.
    Adding a pipeline seeds it with the six Django-CRM default
    stages (PROSPECTING 10% 14d, QUALIFICATION 25% 14d, PROPOSAL
    50% 10d, NEGOTIATION 75% 10d, CLOSED_WON 100%, CLOSED_LOST 0%).
    Removing a pipeline is refused while it still has deals; the
    refusal names the count. Its stages go with it when it has no
    deals.
  * deal — add / list / get / remove / move / close. A deal belongs
    to an erp customer and sits in one stage of one pipeline. It has
    an amount in integer minor units, a currency, a probability
    (0-100, auto-set from the stage when not given: won 100, lost 0,
    else the stage table), an expected close date, a type, a lead
    source and a description. Moving a deal updates the stage
    timestamp and the probability. Closing a deal sets it won or
    lost; a closed deal needs a close date (today when not given) and
    a won deal needs an amount — both refused honestly, the
    Django-CRM clean() rules. A deal's aging is green / yellow / red
    from the days it has sat in its stage against the stage's
    expected days (yellow at expected, red at 1.5x); closed stages
    never age.
  * ticket — add / list / get / remove / respond / close / reopen.
    A support ticket has a status (New, Assigned, Pending, Closed,
    Rejected, Duplicate), a priority (Low, Normal, High, Urgent), a
    case type (Question, Incident, Problem), an optional erp customer
    and a description. Priority sets the SLA targets (first response
    Low 24 / Normal 8 / High 4 / Urgent 1 hours; resolution Low 72 /
    Normal 48 / High 24 / Urgent 4 hours — the Django-CRM workflow
    defaults). `respond` records a note and the first-response time
    (a New ticket becomes Assigned). `close` takes Closed, Rejected
    or Duplicate, sets the close date (today when not given) and the
    resolution time. The SLA clock pauses while a ticket is Pending
    and resumes on any other status. A ticket is breached when its
    deadline passes unresolved, at risk in the last quarter of its
    target.
  * status — counts of pipelines, deals (open / won / lost, open
    value, probability-weighted value, aging red / yellow) and
    tickets (by status and priority, SLA breached / at risk), and
    the ledger chain state.
  * events — the hash-chained ledger (list / verify).

What is NOT built (STOP, stated on the tin):
  * NO WEB APP, no server, no hosted anything, no REST API. The
    tool is local only, single-user by design.
  * NO CUSTOMER / LEAD / INVOICE RECORDS — erp already keeps them;
    this tool only names erp customers.
  * NO MULTI-USER ("Unlimited users" in the Django-CRM description)
    — no VYUK accounts, no staff roles, no teams, no assignment
    beyond the owner's own record. No per-seat anything.
  * NO CUSTOM STAGES — a pipeline is seeded with the six default
    stages; editing stage labels, probabilities or expected days is
    a follow-up, not this batch.
  * NO BUSINESS-HOURS CALENDAR — SLA deadlines are wall-clock
    hours plus the paused seconds, not business hours (Django-CRM
    walks a business-hours calendar; that is a whole subsystem).
  * NO INBOUND EMAIL, no webforms, no macros, no knowledge base,
    no sales goals, no tasks, no automation, no webhooks, no
    sample data. No network call of any kind.

Classification: `deals` writes only to VYUK's own store, so it is
free by design ("app-state", the erp / filament precedent). A deal
amount is a RECORD in integer minor units — the money moved outside
VYUK, the tool never takes payment (the erp invoice precedent).
Unknown or missing action gates fail-closed (the handler refuses
them anyway).

Bounds: at most 20 pipelines; at most 10,000 deals; at most 10,000
tickets; at most 100 notes per ticket; amounts 0-1,000,000,000 minor
units; probability 0-100; names at most 120 chars; descriptions and
notes at most 2,000 chars; lead source and type at most 120 / 64
chars; list limit at most 100.
"""

import json
import math
import re
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path

from .config import DATA_DIR
from . import store_base

_ACTIONS = ("pipeline", "deal", "ticket", "status", "events")

_MAX_PIPELINES = 20
_MAX_DEALS = 10000
_MAX_TICKETS = 10000
_MAX_NOTES = 100
_MAX_CENTS = 1000000000
_MAX_NAME = 120
_MAX_DESC = 2000
_MAX_NOTE = 2000
_MAX_TYPE = 64
_MAX_SOURCE = 120
_MAX_LIMIT = 100

PROPOSED_MARKER = "Parent-ruled (licence, 2026-11-12)"

# An RLock: _do_status / _do_events call verify_chain(), which
# takes the lock again.
_LOCK = threading.RLock()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY_RE = re.compile(r"^[A-Za-z]{3}$")

# The Django-CRM seeded stages: (code, label, kind, probability,
# expected_days). kind is open / won / lost; reports read the kind,
# never the label.
_DEFAULT_STAGES = (
    ("PROSPECTING", "Prospecting", "open", 10, 14),
    ("QUALIFICATION", "Qualification", "open", 25, 14),
    ("PROPOSAL", "Proposal", "open", 50, 10),
    ("NEGOTIATION", "Negotiation", "open", 75, 10),
    ("CLOSED_WON", "Closed Won", "won", 100, None),
    ("CLOSED_LOST", "Closed Lost", "lost", 0, None),
)
_STAGE_PROBABILITY = {code: prob for code, _l, _k, prob, _d
                      in _DEFAULT_STAGES}
_STAGE_KIND = {code: kind for code, _l, kind, _p, _d
               in _DEFAULT_STAGES}
_STAGE_EXPECTED = {code: days for code, _l, _k, _p, days
                   in _DEFAULT_STAGES}
_DEFAULT_PIPELINE_NAME = "Sales"

# Deal aging: a deal is yellow once it has sat `expected_days` whole
# days in its stage and red (rotting) at expected * 1.5 — the
# Django-CRM ROTTEN_MULTIPLIER.
_ROTTEN_MULTIPLIER = 1.5

_TICKET_STATUSES = ("New", "Assigned", "Pending", "Closed",
                    "Rejected", "Duplicate")
_TICKET_TERMINAL = ("Closed", "Rejected", "Duplicate")
_TICKET_PRIORITIES = ("Low", "Normal", "High", "Urgent")
_TICKET_TYPES = ("Question", "Incident", "Problem")

# The Django-CRM workflow SLA defaults, in hours, by priority.
_SLA_FIRST_RESPONSE = {"Low": 24, "Normal": 8, "High": 4, "Urgent": 1}
_SLA_RESOLUTION = {"Low": 72, "Normal": 48, "High": 24, "Urgent": 4}
# How much of a target has to be left for a ticket to still count as
# on track: below this fraction it is "at risk" (the Django-CRM
# SLA_AT_RISK_FRACTION).
_SLA_AT_RISK_FRACTION = 0.25


class _Refusal(Exception):
    pass


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def _clean_str(v, what, limit=_MAX_DESC, required=True):
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


def _clean_date(v, what, required=False, default=None):
    if v is None:
        if required:
            raise _Refusal("%s is required" % what)
        return default
    if not isinstance(v, str):
        raise _Refusal("%s must be a YYYY-MM-DD date" % what)
    v = v.strip()
    if not _DATE_RE.match(v):
        raise _Refusal("%s must be a YYYY-MM-DD date" % what)
    try:
        date.fromisoformat(v)
    except ValueError:
        raise _Refusal("%s is not a real date" % what)
    return v


def _clean_currency(v):
    if v is None:
        return None
    if not isinstance(v, str):
        raise _Refusal("currency must be a 3-letter code")
    v = v.strip().upper()
    if not _CURRENCY_RE.match(v):
        raise _Refusal("currency must be a 3-letter code")
    return v


def _clean_limit(v):
    if v is None:
        return 20
    if not _is_int(v) or v < 1 or v > _MAX_LIMIT:
        raise _Refusal("limit must be an integer between 1 and 100")
    return v


def _fmt_money(cents):
    """Integer minor units -> '1,234.56' (operator's own currency)."""
    sign = "-" if cents < 0 else ""
    n = abs(int(cents))
    return "%s%s.%02d" % (sign, f"{n // 100:,}", n % 100)


def _db_path() -> Path:
    return Path(DATA_DIR) / "deals.sqlite3"


def _erp_db_path() -> Path:
    return Path(DATA_DIR) / "erp.sqlite3"


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS pipelines ("
    " id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,"
    " is_default INTEGER NOT NULL, created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS stages ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " pipeline_id TEXT NOT NULL, code TEXT NOT NULL,"
    " label TEXT NOT NULL, kind TEXT NOT NULL,"
    " order_index INTEGER NOT NULL, expected_days INTEGER,"
    " UNIQUE(pipeline_id, code))",
    "CREATE TABLE IF NOT EXISTS deals ("
    " id TEXT PRIMARY KEY, name TEXT NOT NULL,"
    " customer_id TEXT NOT NULL, customer_name TEXT,"
    " pipeline_id TEXT NOT NULL, stage TEXT NOT NULL,"
    " type TEXT, currency TEXT, amount_cents INTEGER,"
    " probability INTEGER NOT NULL, closed_on TEXT,"
    " lead_source TEXT, description TEXT,"
    " stage_changed_at REAL, created REAL NOT NULL,"
    " updated REAL NOT NULL, is_active INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS tickets ("
    " id TEXT PRIMARY KEY, name TEXT NOT NULL,"
    " status TEXT NOT NULL, priority TEXT NOT NULL,"
    " case_type TEXT, customer_id TEXT, customer_name TEXT,"
    " closed_on TEXT, description TEXT,"
    " first_response_at REAL, resolved_at REAL,"
    " sla_first_response_hours INTEGER,"
    " sla_resolution_hours INTEGER,"
    " sla_paused_at REAL, sla_paused_seconds INTEGER NOT NULL,"
    " created REAL NOT NULL, updated REAL NOT NULL,"
    " is_active INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ticket_notes ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " ticket_id TEXT NOT NULL, body TEXT NOT NULL,"
    " created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS events ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
    " kind TEXT NOT NULL, payload TEXT NOT NULL,"
    " prev_hash TEXT NOT NULL, hash TEXT NOT NULL)",
)


class _DealsStore(store_base.StoreBase):
    """Pipelines + stages + deals + tickets + a hash-chained ledger.
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


def _stage_probability(code, kind):
    """The probability a deal takes on entering a stage."""
    if kind == "won":
        return 100
    if kind == "lost":
        return 0
    return _STAGE_PROBABILITY.get(code, 0)


def _aging_status(kind, expected_days, stage_changed_at, now):
    """green / yellow / red for a deal in its stage.

    Yellow once it has sat `expected_days` whole days, red at
    expected * 1.5. Closed stages and stages with no expected
    duration never age.
    """
    if kind != "open" or not expected_days or not stage_changed_at:
        return "green"
    days = int((now - stage_changed_at) // 86400)
    if days >= int(expected_days * _ROTTEN_MULTIPLIER):
        return "red"
    if days >= expected_days:
        return "yellow"
    return "green"


def _erp_customer(customer):
    """An erp customer id (CUS-0001) or a unique customer name.

    The erp store is read read-only; the deal/ticket keeps the
    customer's name as it was when created, so a later erp removal
    does not rewrite history. Raises _Refusal when the erp store is
    missing or the customer does not exist.
    """
    if customer is None:
        raise _Refusal("customer is required")
    customer = str(customer).strip()
    if not customer:
        raise _Refusal("customer is required")
    erp_path = _erp_db_path()
    if not erp_path.exists():
        raise _Refusal("no erp store yet — add a customer with"
                       " the erp tool first")
    conn = store_base.connect(str(erp_path))
    try:
        row = conn.execute(
            "SELECT id, name FROM customers WHERE id = ?",
            (customer,)).fetchone()
        if row is None:
            rows = conn.execute(
                "SELECT id, name FROM customers WHERE name = ?",
                (customer,)).fetchall()
            if not rows:
                raise _Refusal("no erp customer %r" % customer)
            if len(rows) > 1:
                raise _Refusal(
                    "customer %r is ambiguous (%d customers share the"
                    " name — use its id)" % (customer, len(rows)))
            row = rows[0]
        return row[0], row[1]
    finally:
        conn.close()


def _resolve_pipeline(store, conn, pipeline):
    """A pipeline id (PL-0001), a unique pipeline name, or None for
    the default pipeline."""
    if pipeline is None:
        row = conn.execute(
            "SELECT id, name, is_default, created FROM pipelines"
            " WHERE is_default = 1").fetchone()
        if row is None:
            raise _Refusal("no default pipeline yet — add a pipeline"
                           " first")
        return row
    pipeline = str(pipeline).strip()
    if not pipeline:
        raise _Refusal("pipeline is required")
    row = conn.execute(
        "SELECT id, name, is_default, created FROM pipelines"
        " WHERE id = ?", (pipeline,)).fetchone()
    if row is None:
        rows = conn.execute(
            "SELECT id, name, is_default, created FROM pipelines"
            " WHERE name = ?", (pipeline,)).fetchall()
        if not rows:
            raise _Refusal("no pipeline %r" % pipeline)
        row = rows[0]
    return row


def _resolve_stage(conn, pipeline_id, code):
    """A stage row of a pipeline, by code. None when the pipeline
    does not have that code."""
    if code is None:
        return None
    return conn.execute(
        "SELECT id, pipeline_id, code, label, kind, order_index,"
        " expected_days FROM stages WHERE pipeline_id = ? AND code = ?",
        (pipeline_id, str(code).strip().upper())).fetchone()


def _first_open_stage(conn, pipeline_id):
    return conn.execute(
        "SELECT id, pipeline_id, code, label, kind, order_index,"
        " expected_days FROM stages WHERE pipeline_id = ?"
        " AND kind = 'open' ORDER BY order_index LIMIT 1",
        (pipeline_id,)).fetchone()


def _resolve_deal(store, conn, deal):
    """A deal id (DEL-0001). Deals have no natural name."""
    if deal is None:
        raise _Refusal("deal is required")
    deal = str(deal).strip()
    if not deal:
        raise _Refusal("deal is required")
    row = conn.execute(
        "SELECT id, name, customer_id, customer_name, pipeline_id,"
        " stage, type, currency, amount_cents, probability,"
        " closed_on, lead_source, description, stage_changed_at,"
        " created, updated, is_active FROM deals WHERE id = ?",
        (deal,)).fetchone()
    if row is None:
        raise _Refusal("no deal %r" % deal)
    return row


def _resolve_ticket(store, conn, ticket):
    """A ticket id (TKT-0001). Tickets have no natural name."""
    if ticket is None:
        raise _Refusal("ticket is required")
    ticket = str(ticket).strip()
    if not ticket:
        raise _Refusal("ticket is required")
    row = conn.execute(
        "SELECT id, name, status, priority, case_type, customer_id,"
        " customer_name, closed_on, description, first_response_at,"
        " resolved_at, sla_first_response_hours,"
        " sla_resolution_hours, sla_paused_at, sla_paused_seconds,"
        " created, updated, is_active FROM tickets WHERE id = ?",
        (ticket,)).fetchone()
    if row is None:
        raise _Refusal("no ticket %r" % ticket)
    return row


def _seed_stages(conn, pipeline_id):
    for pos, (code, label, kind, _prob, days) in enumerate(
            _DEFAULT_STAGES, start=1):
        conn.execute(
            "INSERT INTO stages (pipeline_id, code, label, kind,"
            " order_index, expected_days) VALUES (?, ?, ?, ?, ?, ?)",
            (pipeline_id, code, label, kind, pos, days))


def _ensure_seed(store, conn):
    """Create the seeded Sales pipeline (with the six stages) when the
    store has no pipeline yet. Called at the start of a deal add, so
    the first deal always has a default pipeline to land in.
    Idempotent — does nothing once a pipeline exists. Never called
    from a read path (reads never create the store file)."""
    if conn.execute("SELECT COUNT(*) FROM pipelines").fetchone()[0]:
        return
    pid = _next_id(conn, "pipelines", "PL")
    conn.execute(
        "INSERT INTO pipelines (id, name, is_default, created)"
        " VALUES (?, ?, ?, ?)",
        (pid, _DEFAULT_PIPELINE_NAME, 1, time.time()))
    _seed_stages(conn, pid)
    store._append_event_locked(
        conn, "pipeline_seed", {"id": pid, "name": _DEFAULT_PIPELINE_NAME})


def _pipeline_dict(row):
    return {"id": row[0], "name": row[1],
            "is_default": bool(row[2]), "created": row[3]}


def _stage_dict(row):
    return {"pipeline_id": row[1], "code": row[2], "label": row[3],
            "kind": row[4], "order": row[5],
            "expected_days": row[6]}


def _deal_dict(row, store, conn):
    stage = _resolve_stage(conn, row[4], row[5])
    kind = stage[4] if stage else "open"
    expected = stage[6] if stage else None
    now = time.time()
    return {"id": row[0], "name": row[1],
            "customer_id": row[2], "customer": row[3],
            "pipeline_id": row[4], "stage": row[5],
            "stage_kind": kind,
            "type": row[6], "currency": row[7],
            "amount_cents": row[8],
            "amount": _fmt_money(row[8]) if row[8] is not None else None,
            "probability": row[9], "closed_on": row[10],
            "lead_source": row[11], "description": row[12],
            "stage_changed_at": row[13],
            "days_in_stage": (int((now - row[13]) // 86400)
                              if row[13] else 0),
            "aging": _aging_status(kind, expected, row[13], now),
            "created": row[14], "updated": row[15],
            "is_active": bool(row[16])}


def _ticket_sla(row, now):
    """(first_response_breached, resolution_breached, at_risk) for a
    ticket row. Wall-clock hours plus the paused seconds — no
    business-hours calendar (stated on the tin). Terminal tickets
    never breach."""
    if row[2] in _TICKET_TERMINAL:
        return False, False, False
    paused = row[14] or 0
    if row[13] is not None:
        paused += max(int(now - row[13]), 0)
    created = row[15]
    first_deadline = created + (row[11] or 0) * 3600 + paused
    res_deadline = created + (row[12] or 0) * 3600 + paused
    first_breached = row[9] is None and now > first_deadline
    res_breached = row[10] is None and now > res_deadline
    at_risk = False
    if not first_breached and not res_breached:
        # In the last quarter of either target.
        for target, done_at, deadline in (
                (row[11], row[9], first_deadline),
                (row[12], row[10], res_deadline)):
            if target and done_at is None:
                warn_at = deadline - target * 3600 * _SLA_AT_RISK_FRACTION
                if now >= warn_at:
                    at_risk = True
    return first_breached, res_breached, at_risk


def _ticket_dict(row, store, conn):
    now = time.time()
    first_breached, res_breached, at_risk = _ticket_sla(row, now)
    notes = conn.execute(
        "SELECT COUNT(*) FROM ticket_notes WHERE ticket_id = ?",
        (row[0],)).fetchone()[0]
    return {"id": row[0], "name": row[1], "status": row[2],
            "priority": row[3], "case_type": row[4],
            "customer_id": row[5], "customer": row[6],
            "closed_on": row[7], "description": row[8],
            "first_response_at": row[9], "resolved_at": row[10],
            "sla_first_response_hours": row[11],
            "sla_resolution_hours": row[12],
            "sla_paused_seconds": row[14],
            "sla_first_response_breached": first_breached,
            "sla_resolution_breached": res_breached,
            "sla_breached": first_breached or res_breached,
            "sla_at_risk": at_risk,
            "notes": notes,
            "created": row[15], "updated": row[16],
            "is_active": bool(row[17])}


def _do_pipeline(store, kind, args, _db_path=None):
    kind = (kind or "").strip().lower()
    if kind not in ("add", "list", "get", "remove", "set_default"):
        raise _Refusal("unknown pipeline kind %r — add, list, get,"
                       " remove, set_default" % kind)
    if kind in ("list", "get"):
        if store._missing():
            if kind == "list":
                return "ok", "no pipelines yet — the store is empty.", {
                    "pipelines": [], "count": 0}
            raise _Refusal("no pipeline %r" % (args.get("pipeline"),))
        with _LOCK:
            conn = store._connect()
            try:
                if kind == "list":
                    limit = _clean_limit(args.get("limit"))
                    rows = conn.execute(
                        "SELECT id, name, is_default, created"
                        " FROM pipelines ORDER BY created LIMIT ?",
                        (limit,)).fetchall()
                    out = []
                    for r in rows:
                        d = _pipeline_dict(r)
                        d["stages"] = [
                            _stage_dict(s) for s in conn.execute(
                                "SELECT id, pipeline_id, code, label,"
                                " kind, order_index, expected_days"
                                " FROM stages WHERE pipeline_id = ?"
                                " ORDER BY order_index", (r[0],)).fetchall()]
                        out.append(d)
                    return "ok", "%d pipeline(s)." % len(out), {
                        "pipelines": out, "count": len(out)}
                row = _resolve_pipeline(store, conn, args.get("pipeline"))
                d = _pipeline_dict(row)
                d["stages"] = [
                    _stage_dict(s) for s in conn.execute(
                        "SELECT id, pipeline_id, code, label, kind,"
                        " order_index, expected_days FROM stages"
                        " WHERE pipeline_id = ? ORDER BY order_index",
                        (row[0],)).fetchall()]
                return "ok", "pipeline %s." % row[1], {"pipeline": d}
            finally:
                conn.close()
    if kind == "set_default":
        with _LOCK:
            conn = store._connect()
            try:
                row = _resolve_pipeline(store, conn, args.get("pipeline"))
                conn.execute("UPDATE pipelines SET is_default = 0")
                conn.execute(
                    "UPDATE pipelines SET is_default = 1 WHERE id = ?",
                    (row[0],))
                store._append_event_locked(
                    conn, "pipeline_default",
                    {"id": row[0], "name": row[1]})
                conn.commit()
                back = conn.execute(
                    "SELECT id, name, is_default, created"
                    " FROM pipelines WHERE id = ?", (row[0],)).fetchone()
                return "ok", ("pipeline %s is now the default."
                              % row[1]), {"pipeline": _pipeline_dict(back)}
            finally:
                conn.close()
    # kind == "add" or "remove"
    name = _clean_str(args.get("name") or args.get("pipeline"), "name",
                      limit=_MAX_NAME)
    if kind == "add":
        with _LOCK:
            conn = store._connect()
            try:
                if conn.execute(
                        "SELECT COUNT(*) FROM pipelines").fetchone()[0] \
                        >= _MAX_PIPELINES:
                    raise _Refusal("at most %d pipelines" % _MAX_PIPELINES)
                if conn.execute(
                        "SELECT id FROM pipelines WHERE name = ?",
                        (name,)).fetchone() is not None:
                    raise _Refusal("a pipeline named %r already exists"
                                   % name)
                is_default = conn.execute(
                    "SELECT COUNT(*) FROM pipelines WHERE is_default = 1"
                    ).fetchone()[0] == 0
                pid = _next_id(conn, "pipelines", "PL")
                conn.execute(
                    "INSERT INTO pipelines (id, name, is_default,"
                    " created) VALUES (?, ?, ?, ?)",
                    (pid, name, 1 if is_default else 0, time.time()))
                _seed_stages(conn, pid)
                store._append_event_locked(
                    conn, "pipeline_add",
                    {"id": pid, "name": name,
                     "is_default": is_default})
                conn.commit()
                row = conn.execute(
                    "SELECT id, name, is_default, created"
                    " FROM pipelines WHERE id = ?", (pid,)).fetchone()
                if row is None:
                    raise _Refusal("the pipeline was not stored")
                d = _pipeline_dict(row)
                d["stages"] = [
                    _stage_dict(s) for s in conn.execute(
                        "SELECT id, pipeline_id, code, label, kind,"
                        " order_index, expected_days FROM stages"
                        " WHERE pipeline_id = ? ORDER BY order_index",
                        (pid,)).fetchall()]
                return "ok", ("pipeline %s added (%s) with %d stages."
                              % (name, pid, len(d["stages"]))), {
                    "pipeline": d}
            finally:
                conn.close()
    # kind == "remove"
    with _LOCK:
        conn = store._connect()
        try:
            row = _resolve_pipeline(store, conn,
                                    args.get("pipeline") or name)
            n = conn.execute(
                "SELECT COUNT(*) FROM deals WHERE pipeline_id = ?",
                (row[0],)).fetchone()[0]
            if n:
                raise _Refusal(
                    "pipeline %s still has %d deal(s) — move or remove"
                    " them first" % (row[1], n))
            was_default = bool(row[2])
            conn.execute("DELETE FROM stages WHERE pipeline_id = ?",
                         (row[0],))
            conn.execute("DELETE FROM pipelines WHERE id = ?",
                         (row[0],))
            if was_default:
                nxt = conn.execute(
                    "SELECT id FROM pipelines ORDER BY created"
                    " LIMIT 1").fetchone()
                if nxt is not None:
                    conn.execute(
                        "UPDATE pipelines SET is_default = 1"
                        " WHERE id = ?", (nxt[0],))
            store._append_event_locked(
                conn, "pipeline_remove",
                {"id": row[0], "name": row[1]})
            conn.commit()
            gone = conn.execute(
                "SELECT id FROM pipelines WHERE id = ?",
                (row[0],)).fetchone()
            if gone is not None:
                raise _Refusal("the pipeline was not removed")
            return "ok", "pipeline %s removed." % row[1], {
                "pipeline": _pipeline_dict(row)}
        finally:
            conn.close()


def _validate_deal_close(stage_kind, amount_cents, closed_on, today):
    """The Django-CRM clean() rules: a closed deal needs a close
    date, a won deal needs an amount. Returns the close date."""
    if stage_kind in ("won", "lost"):
        if closed_on is None:
            closed_on = today
        if stage_kind == "won" and not amount_cents:
            raise _Refusal("amount is required for a won deal")
    return closed_on


def _do_deal(store, kind, args, _db_path=None):
    kind = (kind or "").strip().lower()
    if kind not in ("add", "list", "get", "remove", "move", "close"):
        raise _Refusal("unknown deal kind %r — add, list, get, remove,"
                       " move, close" % kind)
    if kind in ("list", "get"):
        if store._missing():
            if kind == "list":
                return "ok", "no deals yet — the store is empty.", {
                    "deals": [], "count": 0}
            raise _Refusal("no deal %r" % (args.get("deal"),))
        with _LOCK:
            conn = store._connect()
            try:
                if kind == "list":
                    limit = _clean_limit(args.get("limit"))
                    rows = conn.execute(
                        "SELECT id, name, customer_id, customer_name,"
                        " pipeline_id, stage, type, currency,"
                        " amount_cents, probability, closed_on,"
                        " lead_source, description, stage_changed_at,"
                        " created, updated, is_active FROM deals"
                        " ORDER BY created DESC LIMIT ?",
                        (limit,)).fetchall()
                    return "ok", "%d deal(s)." % len(rows), {
                        "deals": [_deal_dict(r, store, conn)
                                  for r in rows],
                        "count": len(rows)}
                row = _resolve_deal(store, conn, args.get("deal"))
                return "ok", "deal %s." % row[1], {
                    "deal": _deal_dict(row, store, conn)}
            finally:
                conn.close()
    if kind == "add":
        name = _clean_str(args.get("name") or args.get("deal"), "name",
                          limit=_MAX_NAME)
        customer_id, customer_name = _erp_customer(args.get("customer"))
        amount = _clean_int(args.get("amount"), "amount", 0, _MAX_CENTS)
        currency = _clean_currency(args.get("currency"))
        probability = _clean_int(args.get("probability"), "probability",
                                 0, 100)
        closed_on = _clean_date(args.get("closed_on"), "closed_on")
        dtype = _clean_str(args.get("type"), "type", limit=_MAX_TYPE,
                           required=False)
        lead_source = _clean_str(args.get("lead_source"), "lead_source",
                                 limit=_MAX_SOURCE, required=False)
        description = _clean_str(args.get("description"), "description",
                                 limit=_MAX_DESC, required=False)
        with _LOCK:
            conn = store._connect()
            try:
                _ensure_seed(store, conn)
                pipeline = _resolve_pipeline(store, conn,
                                             args.get("pipeline"))
                stage_code = args.get("stage")
                if stage_code is None:
                    stage = _first_open_stage(conn, pipeline[0])
                    if stage is None:
                        raise _Refusal(
                            "pipeline %s has no open stage" % pipeline[1])
                else:
                    stage = _resolve_stage(conn, pipeline[0], stage_code)
                    if stage is None:
                        raise _Refusal(
                            "pipeline %s has no stage %r"
                            % (pipeline[1], stage_code))
                stage_kind = stage[4]
                today = date.today().isoformat()
                closed_on = _validate_deal_close(stage_kind, amount,
                                                 closed_on, today)
                if probability is None:
                    probability = _stage_probability(stage[2], stage_kind)
                if conn.execute(
                        "SELECT COUNT(*) FROM deals").fetchone()[0] \
                        >= _MAX_DEALS:
                    raise _Refusal("at most %d deals" % _MAX_DEALS)
                now = time.time()
                did = _next_id(conn, "deals", "DEL")
                conn.execute(
                    "INSERT INTO deals (id, name, customer_id,"
                    " customer_name, pipeline_id, stage, type, currency,"
                    " amount_cents, probability, closed_on, lead_source,"
                    " description, stage_changed_at, created, updated,"
                    " is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                    " ?, ?, ?, ?, ?, ?, ?)",
                    (did, name, customer_id, customer_name, pipeline[0],
                     stage[2], dtype, currency, amount, probability,
                     closed_on, lead_source, description, now, now, now,
                     1))
                store._append_event_locked(
                    conn, "deal_add",
                    {"id": did, "name": name,
                     "customer_id": customer_id, "stage": stage[2]})
                conn.commit()
                row = conn.execute(
                    "SELECT id, name, customer_id, customer_name,"
                    " pipeline_id, stage, type, currency, amount_cents,"
                    " probability, closed_on, lead_source, description,"
                    " stage_changed_at, created, updated, is_active"
                    " FROM deals WHERE id = ?", (did,)).fetchone()
                if row is None:
                    raise _Refusal("the deal was not stored")
                return "ok", "deal %s added (%s) in %s." % (
                    name, did, stage[2]), {
                    "deal": _deal_dict(row, store, conn)}
            finally:
                conn.close()
    if kind == "remove":
        with _LOCK:
            conn = store._connect()
            try:
                row = _resolve_deal(store, conn, args.get("deal"))
                conn.execute("DELETE FROM deals WHERE id = ?", (row[0],))
                store._append_event_locked(
                    conn, "deal_remove",
                    {"id": row[0], "name": row[1]})
                conn.commit()
                gone = conn.execute(
                    "SELECT id FROM deals WHERE id = ?",
                    (row[0],)).fetchone()
                if gone is not None:
                    raise _Refusal("the deal was not removed")
                return "ok", "deal %s removed." % row[1], {
                    "deal": _deal_dict(row, store, conn)}
            finally:
                conn.close()
    # kind == "move" or "close"
    with _LOCK:
        conn = store._connect()
        try:
            row = _resolve_deal(store, conn, args.get("deal"))
            now = time.time()
            today = date.today().isoformat()
            if kind == "move":
                stage_code = args.get("stage")
                if stage_code is None:
                    raise _Refusal("stage is required to move a deal")
                stage = _resolve_stage(conn, row[4], stage_code)
                if stage is None:
                    raise _Refusal(
                        "pipeline has no stage %r" % stage_code)
                amount = _clean_int(args.get("amount"), "amount",
                                    0, _MAX_CENTS)
                if amount is None:
                    amount = row[8]
                closed_on = _clean_date(args.get("closed_on"),
                                        "closed_on")
                closed_on = _validate_deal_close(stage[4], amount,
                                                 closed_on, today)
                probability = _clean_int(args.get("probability"),
                                         "probability", 0, 100)
                if probability is None:
                    probability = _stage_probability(stage[2], stage[4])
                new_stage = stage[2]
                event = "deal_move"
            else:  # close
                outcome = str(args.get("outcome") or "").strip().lower()
                if outcome not in ("won", "lost"):
                    raise _Refusal("outcome is required to close a deal"
                                   " — won or lost")
                new_stage = ("CLOSED_WON" if outcome == "won"
                             else "CLOSED_LOST")
                stage = _resolve_stage(conn, row[4], new_stage)
                if stage is None:
                    raise _Refusal(
                        "pipeline has no stage %s" % new_stage)
                amount = _clean_int(args.get("amount"), "amount",
                                    0, _MAX_CENTS)
                if amount is None:
                    amount = row[8]
                closed_on = _clean_date(args.get("closed_on"),
                                        "closed_on")
                closed_on = _validate_deal_close(outcome, amount,
                                                 closed_on, today)
                probability = 100 if outcome == "won" else 0
                event = "deal_close"
            conn.execute(
                "UPDATE deals SET stage = ?, amount_cents = ?,"
                " probability = ?, closed_on = ?,"
                " stage_changed_at = ?, updated = ? WHERE id = ?",
                (new_stage, amount, probability, closed_on, now, now,
                 row[0]))
            store._append_event_locked(
                conn, event,
                {"id": row[0], "stage": new_stage,
                 "probability": probability})
            conn.commit()
            back = conn.execute(
                "SELECT id, name, customer_id, customer_name,"
                " pipeline_id, stage, type, currency, amount_cents,"
                " probability, closed_on, lead_source, description,"
                " stage_changed_at, created, updated, is_active"
                " FROM deals WHERE id = ?", (row[0],)).fetchone()
            if back is None or back[5] != new_stage:
                raise _Refusal("the deal was not moved")
            return "ok", "deal %s is now %s (%s)." % (
                row[1], new_stage,
                "won" if back[5] == "CLOSED_WON" else
                "lost" if back[5] == "CLOSED_LOST" else "open"), {
                "deal": _deal_dict(back, store, conn)}
        finally:
            conn.close()


def _ticket_transition(conn, row, new_status, closed_on, now):
    """Apply a ticket status change, keeping the SLA invariants:
    Pending pauses the clock, leaving Pending accumulates the wait,
    a terminal status closes the ticket (close date + resolution
    time), leaving a terminal status reopens it. Returns the new
    (status, closed_on, resolved_at, sla_paused_at,
    sla_paused_seconds)."""
    old_status = row[2]
    paused_at = row[13]
    paused_seconds = row[14] or 0
    resolved_at = row[10]
    if new_status == "Pending" and old_status != "Pending":
        if paused_at is None:
            paused_at = now
    elif old_status == "Pending" and new_status != "Pending":
        if paused_at is not None:
            paused_seconds += max(int(now - paused_at), 0)
            paused_at = None
    if new_status in _TICKET_TERMINAL:
        if closed_on is None:
            closed_on = date.today().isoformat()
        if resolved_at is None:
            resolved_at = now
        if paused_at is not None:
            paused_seconds += max(int(now - paused_at), 0)
            paused_at = None
    elif old_status in _TICKET_TERMINAL:
        closed_on = None
        resolved_at = None
    return (new_status, closed_on, resolved_at, paused_at,
            paused_seconds)


def _do_ticket(store, kind, args, _db_path=None):
    kind = (kind or "").strip().lower()
    if kind not in ("add", "list", "get", "remove", "respond",
                    "close", "reopen", "set_status"):
        raise _Refusal("unknown ticket kind %r — add, list, get,"
                       " remove, respond, close, reopen, set_status"
                       % kind)
    if kind in ("list", "get"):
        if store._missing():
            if kind == "list":
                return "ok", "no tickets yet — the store is empty.", {
                    "tickets": [], "count": 0}
            raise _Refusal("no ticket %r" % (args.get("ticket"),))
        with _LOCK:
            conn = store._connect()
            try:
                if kind == "list":
                    limit = _clean_limit(args.get("limit"))
                    rows = conn.execute(
                        "SELECT id, name, status, priority, case_type,"
                        " customer_id, customer_name, closed_on,"
                        " description, first_response_at, resolved_at,"
                        " sla_first_response_hours,"
                        " sla_resolution_hours, sla_paused_at,"
                        " sla_paused_seconds, created, updated,"
                        " is_active FROM tickets"
                        " ORDER BY created DESC LIMIT ?",
                        (limit,)).fetchall()
                    return "ok", "%d ticket(s)." % len(rows), {
                        "tickets": [_ticket_dict(r, store, conn)
                                    for r in rows],
                        "count": len(rows)}
                row = _resolve_ticket(store, conn, args.get("ticket"))
                notes = conn.execute(
                    "SELECT id, body, created FROM ticket_notes"
                    " WHERE ticket_id = ? ORDER BY created",
                    (row[0],)).fetchall()
                d = _ticket_dict(row, store, conn)
                d["note_list"] = [{"id": n[0], "body": n[1],
                                   "created": n[2]} for n in notes]
                return "ok", "ticket %s." % row[1], {"ticket": d}
            finally:
                conn.close()
    if kind == "add":
        name = _clean_str(args.get("name") or args.get("ticket"), "name",
                          limit=_MAX_NAME)
        status = str(args.get("status") or "New").strip()
        if status not in _TICKET_STATUSES:
            raise _Refusal("status must be one of %s"
                           % ", ".join(_TICKET_STATUSES))
        priority = str(args.get("priority") or "Normal").strip()
        if priority not in _TICKET_PRIORITIES:
            raise _Refusal("priority must be one of %s"
                           % ", ".join(_TICKET_PRIORITIES))
        case_type = args.get("case_type")
        if case_type is not None:
            case_type = str(case_type).strip()
            if case_type not in _TICKET_TYPES:
                raise _Refusal("case_type must be one of %s"
                               % ", ".join(_TICKET_TYPES))
        customer_id = customer_name = None
        if args.get("customer") is not None:
            customer_id, customer_name = _erp_customer(
                args.get("customer"))
        closed_on = _clean_date(args.get("closed_on"), "closed_on")
        description = _clean_str(args.get("description"), "description",
                                 limit=_MAX_DESC, required=False)
        with _LOCK:
            conn = store._connect()
            try:
                now = time.time()
                if status in _TICKET_TERMINAL:
                    if closed_on is None:
                        closed_on = date.today().isoformat()
                    resolved_at = now
                else:
                    resolved_at = None
                paused_at = now if status == "Pending" else None
                if conn.execute(
                        "SELECT COUNT(*) FROM tickets").fetchone()[0] \
                        >= _MAX_TICKETS:
                    raise _Refusal("at most %d tickets" % _MAX_TICKETS)
                tid = _next_id(conn, "tickets", "TKT")
                conn.execute(
                    "INSERT INTO tickets (id, name, status, priority,"
                    " case_type, customer_id, customer_name, closed_on,"
                    " description, first_response_at, resolved_at,"
                    " sla_first_response_hours, sla_resolution_hours,"
                    " sla_paused_at, sla_paused_seconds, created,"
                    " updated, is_active) VALUES (?, ?, ?, ?, ?, ?, ?,"
                    " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (tid, name, status, priority, case_type,
                     customer_id, customer_name, closed_on, description,
                     None, resolved_at, _SLA_FIRST_RESPONSE[priority],
                     _SLA_RESOLUTION[priority], paused_at, 0, now, now,
                     1))
                store._append_event_locked(
                    conn, "ticket_add",
                    {"id": tid, "name": name, "status": status,
                     "priority": priority})
                conn.commit()
                row = conn.execute(
                    "SELECT id, name, status, priority, case_type,"
                    " customer_id, customer_name, closed_on,"
                    " description, first_response_at, resolved_at,"
                    " sla_first_response_hours, sla_resolution_hours,"
                    " sla_paused_at, sla_paused_seconds, created,"
                    " updated, is_active FROM tickets WHERE id = ?",
                    (tid,)).fetchone()
                if row is None:
                    raise _Refusal("the ticket was not stored")
                return "ok", ("ticket %s added (%s, %s, %s priority)."
                              % (name, tid, status, priority)), {
                    "ticket": _ticket_dict(row, store, conn)}
            finally:
                conn.close()
    if kind == "remove":
        with _LOCK:
            conn = store._connect()
            try:
                row = _resolve_ticket(store, conn, args.get("ticket"))
                conn.execute(
                    "DELETE FROM ticket_notes WHERE ticket_id = ?",
                    (row[0],))
                conn.execute("DELETE FROM tickets WHERE id = ?",
                             (row[0],))
                store._append_event_locked(
                    conn, "ticket_remove",
                    {"id": row[0], "name": row[1]})
                conn.commit()
                gone = conn.execute(
                    "SELECT id FROM tickets WHERE id = ?",
                    (row[0],)).fetchone()
                if gone is not None:
                    raise _Refusal("the ticket was not removed")
                return "ok", "ticket %s removed." % row[1], {
                    "ticket": _ticket_dict(row, store, conn)}
            finally:
                conn.close()
    if kind == "respond":
        note = _clean_str(args.get("note"), "note", limit=_MAX_NOTE)
        with _LOCK:
            conn = store._connect()
            try:
                row = _resolve_ticket(store, conn, args.get("ticket"))
                if row[2] in _TICKET_TERMINAL:
                    raise _Refusal(
                        "ticket %s is %s — reopen it before responding"
                        % (row[1], row[2]))
                n = conn.execute(
                    "SELECT COUNT(*) FROM ticket_notes"
                    " WHERE ticket_id = ?", (row[0],)).fetchone()[0]
                if n >= _MAX_NOTES:
                    raise _Refusal("at most %d notes per ticket"
                                   % _MAX_NOTES)
                now = time.time()
                conn.execute(
                    "INSERT INTO ticket_notes (ticket_id, body, created)"
                    " VALUES (?, ?, ?)", (row[0], note, now))
                first_response_at = row[9] if row[9] is not None else now
                new_status = ("Assigned" if row[2] == "New" else row[2])
                conn.execute(
                    "UPDATE tickets SET first_response_at = ?,"
                    " status = ?, updated = ? WHERE id = ?",
                    (first_response_at, new_status, now, row[0]))
                store._append_event_locked(
                    conn, "ticket_respond",
                    {"id": row[0], "first_response": row[9] is None})
                conn.commit()
                back = conn.execute(
                    "SELECT id, name, status, priority, case_type,"
                    " customer_id, customer_name, closed_on,"
                    " description, first_response_at, resolved_at,"
                    " sla_first_response_hours, sla_resolution_hours,"
                    " sla_paused_at, sla_paused_seconds, created,"
                    " updated, is_active FROM tickets WHERE id = ?",
                    (row[0],)).fetchone()
                if back is None or back[9] is None:
                    raise _Refusal("the response was not recorded")
                return "ok", ("response recorded on ticket %s."
                              % row[1]), {
                    "ticket": _ticket_dict(back, store, conn)}
            finally:
                conn.close()
    # kind == "close", "reopen" or "set_status"
    with _LOCK:
        conn = store._connect()
        try:
            row = _resolve_ticket(store, conn, args.get("ticket"))
            now = time.time()
            if kind == "close":
                new_status = str(args.get("status") or "Closed").strip()
                if new_status not in _TICKET_TERMINAL:
                    raise _Refusal(
                        "close takes a terminal status — Closed,"
                        " Rejected or Duplicate")
                closed_on = _clean_date(args.get("closed_on"),
                                        "closed_on")
                event = "ticket_close"
            elif kind == "reopen":
                if row[2] not in _TICKET_TERMINAL:
                    raise _Refusal(
                        "ticket %s is %s — only a closed ticket can be"
                        " reopened" % (row[1], row[2]))
                new_status = "Assigned"
                closed_on = None
                event = "ticket_reopen"
            else:  # set_status
                new_status = str(args.get("status") or "").strip()
                if new_status not in _TICKET_STATUSES:
                    raise _Refusal("status must be one of %s"
                                   % ", ".join(_TICKET_STATUSES))
                closed_on = _clean_date(args.get("closed_on"),
                                        "closed_on")
                event = "ticket_set_status"
            (new_status, closed_on, resolved_at, paused_at,
             paused_seconds) = _ticket_transition(
                conn, row, new_status, closed_on, now)
            conn.execute(
                "UPDATE tickets SET status = ?, closed_on = ?,"
                " resolved_at = ?, sla_paused_at = ?,"
                " sla_paused_seconds = ?, updated = ? WHERE id = ?",
                (new_status, closed_on, resolved_at, paused_at,
                 paused_seconds, now, row[0]))
            store._append_event_locked(
                conn, event,
                {"id": row[0], "status": new_status})
            conn.commit()
            back = conn.execute(
                "SELECT id, name, status, priority, case_type,"
                " customer_id, customer_name, closed_on,"
                " description, first_response_at, resolved_at,"
                " sla_first_response_hours, sla_resolution_hours,"
                " sla_paused_at, sla_paused_seconds, created,"
                " updated, is_active FROM tickets WHERE id = ?",
                (row[0],)).fetchone()
            if back is None or back[2] != new_status:
                raise _Refusal("the ticket status was not changed")
            return "ok", "ticket %s is now %s." % (row[1], new_status), {
                "ticket": _ticket_dict(back, store, conn)}
        finally:
            conn.close()


def _do_status(store, args, _db_path=None):
    if store._missing():
        return ("ok", "no deals or tickets yet — the store is empty.",
                {"pipelines": 0, "deals": 0, "tickets": 0,
                 "chain_ok": True, "events": 0,
                 "chain_tip": "the store file does not exist yet"})
    ok, n = store.verify_chain()
    with _LOCK:
        conn = store._connect()
        try:
            pipelines = conn.execute(
                "SELECT COUNT(*) FROM pipelines").fetchone()[0]
            deals = conn.execute(
                "SELECT stage, amount_cents, probability,"
                " stage_changed_at FROM deals").fetchall()
            tickets = conn.execute(
                "SELECT id, name, status, priority, case_type,"
                " customer_id, customer_name, closed_on, description,"
                " first_response_at, resolved_at,"
                " sla_first_response_hours, sla_resolution_hours,"
                " sla_paused_at, sla_paused_seconds, created, updated,"
                " is_active FROM tickets").fetchall()
        finally:
            conn.close()
    now = time.time()
    open_deals = won = lost = 0
    open_value = weighted = 0
    aging_red = aging_yellow = 0
    for stage, amount, prob, changed in deals:
        kind = _STAGE_KIND.get(stage, "open")
        expected = _STAGE_EXPECTED.get(stage)
        if kind == "won":
            won += 1
        elif kind == "lost":
            lost += 1
        else:
            open_deals += 1
            if amount:
                open_value += amount
                weighted += amount * (prob or 0) // 100
            aging = _aging_status(kind, expected, changed, now)
            if aging == "red":
                aging_red += 1
            elif aging == "yellow":
                aging_yellow += 1
    by_status = {}
    by_priority = {}
    breached = at_risk = 0
    for row in tickets:
        by_status[row[2]] = by_status.get(row[2], 0) + 1
        by_priority[row[3]] = by_priority.get(row[3], 0) + 1
        first_b, res_b, risk = _ticket_sla(row, now)
        if first_b or res_b:
            breached += 1
        elif risk:
            at_risk += 1
    return "ok", (
        "%d pipeline(s), %d deal(s) (%d open, %d won, %d lost),"
        " %d ticket(s), %d SLA breached, %d at risk."
        % (pipelines, len(deals), open_deals, won, lost,
           len(tickets), breached, at_risk)), {
        "pipelines": pipelines,
        "deals": len(deals), "deals_open": open_deals,
        "deals_won": won, "deals_lost": lost,
        "open_value_cents": open_value,
        "open_value": _fmt_money(open_value),
        "weighted_value_cents": weighted,
        "weighted_value": _fmt_money(weighted),
        "aging_red": aging_red, "aging_yellow": aging_yellow,
        "tickets": len(tickets), "tickets_by_status": by_status,
        "tickets_by_priority": by_priority,
        "sla_breached": breached, "sla_at_risk": at_risk,
        "chain_ok": ok, "events": n}


def _do_events(store, args, _db_path=None):
    kind = (args.get("kind") or "list").strip().lower()
    if kind == "verify":
        ok, n = store.verify_chain()
        if not ok:
            return ("error",
                    "the ledger chain does not verify over %d event(s)."
                    % n, None)
        return "ok", "ledger verifies over %d event(s)." % n, {
            "ok": True, "events": n}
    if kind != "list":
        raise _Refusal("unknown events kind %r — list or verify" % kind)
    if store._missing():
        return "ok", "no events yet — the store is empty.", {
            "events": [], "count": 0}
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


def _handler(action=None, kind=None, pipeline=None, stage=None,
             deal=None, ticket=None, customer=None, name=None,
             amount=None, currency=None, probability=None,
             closed_on=None, type=None, status=None, priority=None,
             case_type=None, description=None, lead_source=None,
             outcome=None, note=None, limit=None,
             _store=None, _db_path=None):
    """The funnel entry point for `deals`.

    action: pipeline | deal | ticket | status | events. kind: the
    sub-action (add/list/get/remove/set_default/move/close/respond/
    reopen/set_status/verify). The rest are the named arguments.
    """
    store = _store or _DealsStore(_db_path)
    args = {"kind": kind, "pipeline": pipeline, "stage": stage,
            "deal": deal, "ticket": ticket, "customer": customer,
            "name": name, "amount": amount, "currency": currency,
            "probability": probability, "closed_on": closed_on,
            "type": type, "status": status, "priority": priority,
            "case_type": case_type, "description": description,
            "lead_source": lead_source, "outcome": outcome,
            "note": note, "limit": limit}
    try:
        action = _clean_str(action, "action", limit=20)
        if action not in _ACTIONS:
            raise _Refusal("unknown action %r — %s"
                           % (action, ", ".join(_ACTIONS)))
        if action == "pipeline":
            status, msg, data = _do_pipeline(store, kind, args, _db_path)
        elif action == "deal":
            status, msg, data = _do_deal(store, kind, args, _db_path)
        elif action == "ticket":
            status, msg, data = _do_ticket(store, kind, args, _db_path)
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
        name="deals",
        description=("Deal pipeline and support tickets on erp"
                     " customers: deals move through pipeline stages"
                     " with probability and aging, support tickets"
                     " carry priority and SLA targets, over a"
                     " hash-chained ledger."),
        handler=_handler,
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "pipeline, deal, ticket,"
                                          " status or events",
                           "enum": list(_ACTIONS)},
                "kind": {"type": "string",
                         "description": "the sub-action: add, list,"
                                        " get, remove, set_default,"
                                        " move, close, respond, reopen"
                                        " or set_status"},
                "pipeline": {"type": "string",
                             "description": "a pipeline id (PL-0001)"
                                            " or a pipeline name"},
                "stage": {"type": "string",
                          "description": "a stage code (PROSPECTING,"
                                         " QUALIFICATION, PROPOSAL,"
                                         " NEGOTIATION, CLOSED_WON,"
                                         " CLOSED_LOST)"},
                "deal": {"type": "string",
                         "description": "a deal id (DEL-0001)"},
                "ticket": {"type": "string",
                           "description": "a ticket id (TKT-0001)"},
                "customer": {"type": "string",
                             "description": "an erp customer id"
                                            " (CUS-0001) or a unique"
                                            " customer name"},
                "name": {"type": "string",
                         "description": "a pipeline, deal or ticket"
                                        " name"},
                "amount": {"type": "integer",
                           "description": "the deal amount in integer"
                                          " minor units (0-1000000000)"},
                "currency": {"type": "string",
                             "description": "the deal currency as a"
                                            " 3-letter code"},
                "probability": {"type": "integer",
                                "description": "the deal probability"
                                               " in percent (0-100;"
                                               " auto-set from the"
                                               " stage when not given)"},
                "closed_on": {"type": "string",
                              "description": "the close date as"
                                             " YYYY-MM-DD (today when"
                                             " not given on close)"},
                "type": {"type": "string",
                         "description": "the deal type"},
                "status": {"type": "string",
                           "description": "the ticket status (New,"
                                          " Assigned, Pending, Closed,"
                                          " Rejected or Duplicate)"},
                "priority": {"type": "string",
                             "description": "the ticket priority (Low,"
                                            " Normal, High or Urgent)"},
                "case_type": {"type": "string",
                              "description": "the ticket type"
                                             " (Question, Incident or"
                                             " Problem)"},
                "description": {"type": "string",
                                "description": "a deal or ticket"
                                               " description"},
                "lead_source": {"type": "string",
                                "description": "where the deal came"
                                               " from"},
                "outcome": {"type": "string",
                            "description": "the deal close outcome"
                                           " (won or lost)"},
                "note": {"type": "string",
                         "description": "a response note on a ticket"},
                "limit": {"type": "integer",
                          "description": "the maximum rows to return"
                                         " (1-100)"},
            },
            "required": ["action"],
        },
    )
