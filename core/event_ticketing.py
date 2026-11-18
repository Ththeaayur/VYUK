"""Local event ticketing over the existing event-planning pack
(R18-7, Attendize/Attendize inspiration).

Parent-ruled (licence, 2026-11-12): Attendize/Attendize
(Attribution Assurance License, read in full. No code adopted,
name not used.)

The finding (Attendize, rebuilt from scratch — no code adopted):
an open-source ticketing and event management application built
using the Laravel PHP framework. It lets event organisers sell
tickets to their events and manage attendees without paying
service fees to third party ticketing companies. Its shape,
verified from source (the README on the develop branch): event
pages, attendee management (refunds, messaging), XLS/CSV export,
a print-friendly attendee list, unlimited organisers/events,
real-time statistics, multiple currencies, checkout, QR-code
tickets, brandable pages, affiliate tracking, and payment
gateways (Stripe, PayPal, Coinbase) plus offline payments.

What existed before this batch (all read on the backup first):

  * The event-planning pack (core/event_planning.py, R16-8)
    holds plans, guests, vendors, a budget estimator,
    milestones and a run-of-show — but no ticket of any kind.
  * Zero ticketing surface anywhere in core/ or gui/
    (parent-verified full-list greps on the backup: "ticket
    selling", "ticket sale", "attendee check-in", "event
    ticket", "ticketing" and "attendize" all 0; the "ticket"
    hits are approval_ticket (the R18-1 refund reference),
    travel tickets and support-ticket claims; the "check-in"
    hits are mood check-ins and proactive check-ins).
  * The tree's own QR encoder (core/qr_encode.py, the kovrel
    precedent) can render a ticket code as a PNG — nothing
    wires it to an event.

What this builds, and only this remainder:

  * TICKET TYPES per plan: name, recorded price, capacity
    (0 = unlimited) and a description. A price is a number
    the owner types in — a recorded quote, never a payment,
    never a price feed.
  * SALE RECORDS: a sale is a record the owner made (or was
    told about) — buyer, ticket type, quantity, the recorded
    unit price, the recorded total, and how the buyer paid,
    noted as a record ("cash", "bank transfer", "at the
    door"). The money moved outside VYUK; the tool never
    takes payment, never talks to a gateway, never charges
    a card. Each ticket in a sale gets its own code.
  * QR-CODE TICKETS: each ticket code is rendered as a QR
    PNG with the tree's own qr_encode (Pillow is the tree's
    declared image library) under the app's own data dir.
    The QR is a convenience — the CODE is the check-in key,
    and a ticket still exists when its QR could not render.
  * CHECK-IN by ticket code: a local record (code, buyer,
    type, time). A duplicate check-in is refused honestly
    ("already checked in at ..."), an unknown code is refused
    honestly, and a voided sale's tickets cannot check in.
  * A capacity guard: a sale that would oversell a type is
    refused with the numbers (sold / capacity / left).
  * STATUS counts per plan and a hash-chained event ledger
    (issue / check-in / void), verified in full by the
    `events` action.

What is NOT built here, and why:

  * The HOSTED ticketing site (public event pages, a
    checkout, an embeddable ticket widget, public event
    listings). A shareable page needs a hosting / publishing
    shape VYUK does not have — the public-page ruling (static
    page + message request) covers a booking request, not a
    live ticket shop.
  * PAYMENT CAPTURE (Stripe / PayPal / Coinbase, a checkout,
    a service charge, a refund as money movement). Money
    movement is ruling 34 territory, OPEN. A sale record can
    be VOIDED as a local record (the tickets stop working);
    any refund was handled outside VYUK and is only noted.
  * Multi-organiser accounts (the multi-user ruling: no VYUK
    accounts; the owner is the only operator).
  * Messaging attendees, affiliate tracking, XLS/CSV export
    of the attendee list (an export is a small follow-up
    once a target format is named).

Trust discipline (stated plainly, same shape as the
event-planning pack this builds on):

  * Local only: ticket types, sales and check-ins live in
    the app's own memory store (one fact per plan, category
    "event_tickets", the event_plans shape); the ledger is
    the app's own SQLite file; QR PNGs are local files. No
    network, no egress, no second app, no new dependency
    (stdlib + the tree's own qr_encode + Pillow, which the
    tree already declares).
  * Free by design ("app-state", the event_plan /
    budget_envelope precedent): the tool writes only to
    VYUK's own store, so it asks nothing. No money moves
    anywhere here — a sale is a record, and the recorded
    total is labelled as recorded, never as a payment.
  * Every write is verified before it is claimed (A1-store):
    save_fact returns True only when its own read-back
    proves the row landed, and the handler re-reads the
    ticketing fact and confirms the added record is present
    — a write that does not verify is reported as a failure,
    never claimed. The ledger is hash-chained and verified
    in full by the `events` action.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from .config import DATA_DIR
from . import store_base
from .skills_money_helpers import _coerce_float, _money, _store

_TICKETS_CATEGORY = "event_tickets"
_DB_FILENAME = "event_tickets.sqlite3"

_MAX_NAME_CHARS = 200
_MAX_DESC_CHARS = 500
_MAX_BUYER_CHARS = 200
_MAX_PAY_METHOD_CHARS = 50
_MAX_PAY_REF_CHARS = 100
_MAX_CODE_CHARS = 50
_MAX_TYPES = 50
_MAX_SALES = 500
_MAX_QTY = 100
_MAX_PRICE = 1e9
_MAX_CAPACITY = 100000
_MAX_LIST = 50

_ACTIONS = ("type", "sale", "checkin", "status", "events")
_TYPE_MODES = ("add", "list", "get", "remove")
_SALE_MODES = ("add", "list", "get", "remove")
_CHECKIN_MODES = ("checkin", "list")


# ---------------------------------------------------------------------------
# Store — one memory fact per plan (the event_plans shape)
# ---------------------------------------------------------------------------


def _db_path():
    return Path(DATA_DIR) / _DB_FILENAME


def _qr_root():
    """Where ticket QR PNGs live (seam: tests point this at a
    temp dir; handlers never touch DATA_DIR directly)."""
    return Path(DATA_DIR) / "event_tickets"


def _load_ticketing() -> tuple[dict, int]:
    """(ticketing by lower-cased plan name, broken_count): every
    stored ticketing fact parsed; unparseable or misshapen ones
    counted."""
    records: dict[str, dict] = {}
    broken = 0
    try:
        facts = [f for f in _store().get_all_facts()
                 if f.get("category") == _TICKETS_CATEGORY]
    except Exception:
        return records, 1
    for fact in facts:
        try:
            rec = json.loads(fact.get("value") or "")
            if not isinstance(rec, dict):
                raise ValueError("ticketing is not an object")
            for key in ("plan", "types", "sales", "next_type_id",
                        "next_sale_id", "next_code"):
                if key not in rec:
                    raise ValueError("ticketing is missing %r" % key)
            for key in ("types", "sales"):
                if not isinstance(rec[key], list):
                    raise ValueError("ticketing %r is not a list" % key)
            records[str(rec["plan"]).strip().lower()] = rec
        except Exception:  # noqa: BLE001 — one broken fact must not
            broken += 1    # take the whole ticketing surface down
    return records, broken


def _save_ticketing(rec: dict) -> bool:
    """Persist one plan's ticketing and verify it landed
    (A1-store): save_fact returns True only when its own
    read-back proves the row landed, and the re-read here must
    parse back to the same plan name. False means the write
    did not verify."""
    key = str(rec.get("plan") or "").strip()
    if not key:
        return False
    try:
        landed = _store().save_fact(
            _TICKETS_CATEGORY, key, json.dumps(rec))
        if not landed:
            return False
        back = _store().get_fact(_TICKETS_CATEGORY, key)
        if back is None:
            return False
        reread = json.loads(back.get("value") or "")
        return (isinstance(reread, dict)
                and str(reread.get("plan") or "").strip() == key)
    except Exception:  # noqa: BLE001
        return False


def _find_ticketing(records: dict, plan: str):
    wanted = str(plan or "").strip().lower()
    if not wanted:
        return None
    return records.get(wanted)


def _new_ticketing(plan: str) -> dict:
    return {
        "plan": plan,
        "types": [],
        "sales": [],
        "next_type_id": 1,
        "next_sale_id": 1,
        "next_code": 1,
    }


def _plan_exists(name: str) -> bool:
    """True when an event plan of this name exists (reads the
    event_plans facts through this module's own _store seam —
    the plan itself is owned by core/event_planning.py)."""
    wanted = str(name or "").strip().lower()
    if not wanted:
        return False
    try:
        facts = [f for f in _store().get_all_facts()
                 if f.get("category") == "event_plans"]
    except Exception:
        return False
    for fact in facts:
        try:
            plan = json.loads(fact.get("value") or "")
            if isinstance(plan, dict):
                if str(plan.get("name") or "").strip().lower() == wanted:
                    return True
        except Exception:  # noqa: BLE001 — a broken plan fact is
            pass            # not this plan; keep looking
        if str(fact.get("key") or "").strip().lower() == wanted:
            return True
    return False


# ---------------------------------------------------------------------------
# Validators — every refusal names what was wrong and what to give
# ---------------------------------------------------------------------------


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _coerce_int(value):
    try:
        if isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _broken_note(broken: int) -> str:
    if not broken:
        return ""
    return (f" ({broken} stored ticketing record"
            f"{'s' if broken != 1 else ''} couldn't be read and "
            f"{'were' if broken != 1 else 'was'} skipped.)")


def _plan_slug(plan: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(plan or "").lower()).strip("-")
    return slug or "plan"


def _ticket_code(plan: str, seq: int) -> str:
    """One admission's code: the plan's first three letters
    (upper-cased, X-padded) + a zero-padded sequence, e.g.
    SPR-0001. Unique per plan; the code is the check-in key."""
    letters = "".join(
        ch for ch in str(plan or "").upper() if ch.isalnum())[:3]
    letters = (letters + "XXX")[:3]
    return "%s-%04d" % (letters, seq)


def _type_counts(rec: dict, type_id: int) -> tuple[int, int]:
    """(sold, checked_in) for one type: non-void sales only."""
    sold = 0
    checked = 0
    for sale in (rec.get("sales") or []):
        if sale.get("void") or sale.get("type_id") != type_id:
            continue
        for ticket in (sale.get("tickets") or []):
            sold += 1
            if ticket.get("checked_in"):
                checked += 1
    return sold, checked


def _find_ticket(rec: dict, code: str):
    """(sale, ticket) for one code, or (None, None)."""
    wanted = str(code or "").strip().upper()
    if not wanted:
        return None, None
    for sale in (rec.get("sales") or []):
        for ticket in (sale.get("tickets") or []):
            if str(ticket.get("code") or "").strip().upper() == wanted:
                return sale, ticket
    return None, None


def _render_qr(code: str, plan: str):
    """Render one ticket code as a QR PNG under the app's own
    data dir; returns the path, or None when the QR could not
    render (the ticket still exists — the code is the check-in
    key and the QR is a convenience, the companion.py
    precedent)."""
    try:
        from . import qr_encode as _qr

        root = _qr_root() / _plan_slug(plan)
        root.mkdir(parents=True, exist_ok=True)
        path = root / ("qr_%s.png" % code)
        _qr.render_png(code, path)
        return str(path)
    except Exception:  # noqa: BLE001 — QR is a convenience
        return None


# ---------------------------------------------------------------------------
# Ledger — hash-chained events (the voice_agent / agent_call /
# prompt_tune shape: every ticket issue, check-in and void is an
# auditable event; the `events` action verifies the full chain)
# ---------------------------------------------------------------------------


def _get_ledger():
    """The ticketing ledger (seam: tests substitute a temp-path
    ledger by replacing this function; handlers never touch the
    database file directly)."""
    return _Ledger()


def _locked(method):
    """Serialise ledger access across threads (an RLock, not a
    Lock, so a method may call another locked method)."""

    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class _Ledger:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else _db_path()
        self._lock = threading.RLock()
        self.conn = None
        # The file is NOT created here: a read (list_events /
        # verify_chain) must not create the database — it is
        # created on the first real write (append_event).

    def _open(self):
        if self.conn is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = store_base.connect(str(self.path),
                                     check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            "id INTEGER PRIMARY KEY, seq INTEGER, ts REAL, kind TEXT, "
            "payload_json TEXT, prev_hash TEXT, hash TEXT)")
        self.conn.commit()

    @_locked
    def close(self):
        try:
            if self.conn is not None:
                self.conn.close()
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.conn = None

    @_locked
    def append_event(self, kind, payload):
        self._open()
        row = self.conn.execute(
            "SELECT seq, hash FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        prev_hash, seq = ("GENESIS", 0) if row is None else (row[1], row[0])
        seq = seq + 1
        ts = time.time()
        payload_json = json.dumps(payload, sort_keys=True)
        digest = store_base.chain_digest(
            "pipe_repr", prev_hash,
            {"seq": seq, "ts": ts, "kind": kind,
             "payload_json": payload_json})
        self.conn.execute(
            "INSERT INTO events (seq, ts, kind, payload_json, prev_hash, "
            "hash) VALUES (?, ?, ?, ?, ?, ?)",
            (seq, ts, kind, payload_json, prev_hash, digest))
        self.conn.commit()
        return seq

    @_locked
    def list_events(self, limit=20):
        # A missing file means no events, not an error — and a
        # read must not create the database.
        if not self.path.exists():
            return []
        self._open()
        rows = self.conn.execute(
            "SELECT seq, ts, kind, payload_json FROM events "
            "ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [{"seq": r[0], "ts": r[1], "kind": r[2],
                 "payload": json.loads(r[3])} for r in rows]

    @_locked
    def verify_chain(self):
        """Full-chain verification: every hash must recompute from
        its predecessor. Returns (True, "") or (False, reason)."""
        # A missing file means no events — trivially verified.
        if not self.path.exists():
            return True, ""
        self._open()
        rows = self.conn.execute(
            "SELECT seq, ts, kind, payload_json, prev_hash, hash "
            "FROM events ORDER BY seq").fetchall()
        entries = [{"seq": seq, "ts": ts, "kind": kind,
                    "payload_json": payload_json,
                    "prev_hash": prev_hash, "hash": digest}
                   for seq, ts, kind, payload_json, prev_hash,
                   digest in rows]
        result = store_base.verify_entries(
            entries, "pipe_repr", store_base.LEGACY_GENESIS)
        if not result["ok"]:
            bad = rows[result["first_break"]["index"]]
            if result["first_break"]["reason"].startswith("link break"):
                return False, "event %d: prev_hash does not match" % bad[0]
            return False, "event %d: hash does not recompute" % bad[0]
        return True, ""


def _ledger_note(ledger, kind, payload) -> str:
    """Append one ledger event; the store write is the source of
    truth, so a dead ledger never fails the operation — it is
    reported in the reply instead."""
    try:
        ledger.append_event(kind, payload)
        return ""
    except Exception as e:  # noqa: BLE001
        return " (the ledger event could not be recorded: %s)" % e


def _fmt_time(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "unknown time"


# ---------------------------------------------------------------------------
# event_tickets — ticket types, sale records, QR check-in
# ---------------------------------------------------------------------------


def event_tickets(action: str = "", mode: str = "", plan: str = "",
                  type_id=0, sale_id=0, name: str = "", price=0,
                  capacity=0, description: str = "", buyer: str = "",
                  qty=0, pay_method: str = "", pay_ref: str = "",
                  code: str = "", limit=0) -> str:
    """Ticket types, sale records and QR check-in for one event
    plan. A sale is a record the owner made — the money moved
    outside VYUK, so the tool never takes payment."""
    action = str(action or "").strip().lower()
    if action not in _ACTIONS:
        return ("event_tickets actions are 'type', 'sale', 'checkin', "
                "'status' and 'events' — e.g. event_tickets with "
                "action 'type', mode 'add', a plan like 'Spring "
                "wedding' and a type name like 'General Admission'.")
    clean_plan = _clean(plan, _MAX_NAME_CHARS)
    if not clean_plan:
        return "That needs the plan's name."
    if not _plan_exists(clean_plan):
        return (f"No plan called '{clean_plan}'. Create it first "
                f"with event_plan, action 'create'.")
    records, broken = _load_ticketing()
    broken_note = _broken_note(broken)
    rec = _find_ticketing(records, clean_plan)
    if action == "events":
        return _events_action(limit, broken_note, clean_plan)
    if action == "status":
        return _status_action(rec, clean_plan, broken_note)
    mode = str(mode or "").strip().lower()
    if action == "type":
        if mode not in _TYPE_MODES:
            return ("Ticket-type modes are 'add', 'list', 'get' and "
                    "'remove'.")
        return _type_action(mode, rec, clean_plan, type_id, name,
                            price, capacity, description, broken_note)
    if action == "sale":
        if mode not in _SALE_MODES:
            return ("Sale modes are 'add', 'list', 'get' and "
                    "'remove' (remove voids the sale record).")
        return _sale_action(mode, rec, clean_plan, type_id, sale_id,
                            buyer, qty, pay_method, pay_ref,
                            broken_note)
    return _checkin_action(mode, rec, clean_plan, code, broken_note)


def _events_action(limit, broken_note, plan=None) -> str:
    wanted = _coerce_int(limit)
    if wanted is None or wanted < 1:
        wanted = 20
    wanted = min(wanted, 100)
    ledger = _get_ledger()
    try:
        ok, reason = ledger.verify_chain()
        # The ledger is shared across plans, so only this plan's
        # events are shown (every payload carries its plan).
        # Fetch generously, then filter, then slice to the limit.
        raw = ledger.list_events(max(200, wanted * 10))
    finally:
        ledger.close()
    if not ok:
        return (f"The ticket ledger FAILED verification: {reason}. "
                f"Treat its events as untrusted.{broken_note}")
    events = [ev for ev in raw
              if plan is None
              or (ev.get("payload") or {}).get("plan") == plan][:wanted]
    if not events:
        return f"No ticket events yet.{broken_note}"
    lines = [f"Ticket events ({len(events)} shown, ledger verified — "
             f"the full hash chain recomputes):{broken_note}"]
    for ev in events:
        payload = ev.get("payload") or {}
        summary = ", ".join(
            "%s=%s" % (k, v) for k, v in sorted(payload.items()))
        lines.append(f"  • #{ev['seq']} {_fmt_time(ev['ts'])} "
                     f"{ev['kind']} ({summary})")
    return "\n".join(lines)


def _status_action(rec, clean_plan, broken_note) -> str:
    if rec is None:
        return (f"No tickets on '{clean_plan}' yet — add a ticket "
                f"type first with action 'type', mode 'add'."
                f"{broken_note}")
    types = rec.get("types") or []
    sales = rec.get("sales") or []
    live_sales = [s for s in sales if not s.get("void")]
    void_sales = len(sales) - len(live_sales)
    sold = sum(len(s.get("tickets") or []) for s in live_sales)
    checked = sum(1 for s in live_sales for t in (s.get("tickets") or [])
                  if t.get("checked_in"))
    recorded = sum(float(s.get("total") or 0) for s in live_sales)
    lines = [f"Tickets on '{clean_plan}': {len(types)} type(s), "
             f"{len(live_sales)} live sale(s)"
             f"{f' ({void_sales} voided)' if void_sales else ''}, "
             f"{sold} ticket(s) issued, {checked} checked in, "
             f"{sold - checked} still to arrive. Recorded sales "
             f"total {_money(recorded)} — money the owner recorded, "
             f"not a payment total.{broken_note}"]
    for t in types:
        t_sold, t_checked = _type_counts(rec, int(t.get("id") or 0))
        cap = int(t.get("capacity") or 0)
        left = "unlimited" if cap == 0 else str(max(cap - t_sold, 0))
        lines.append(f"  • {t.get('name')} — {_money(float(t.get('price') or 0))} "
                     f"each, {t_sold} sold, {t_checked} checked in, "
                     f"{left} left")
    return "\n".join(lines)


def _type_action(mode, rec, clean_plan, type_id, name, price,
                 capacity, description, broken_note) -> str:
    if mode == "list":
        if rec is None or not (rec.get("types") or []):
            return (f"No ticket types on '{clean_plan}' yet — add one "
                    f"with action 'type', mode 'add'.{broken_note}")
        lines = [f"Ticket types on '{clean_plan}':{broken_note}"]
        for t in (rec.get("types") or []):
            t_sold, t_checked = _type_counts(rec, int(t.get("id") or 0))
            cap = int(t.get("capacity") or 0)
            left = "unlimited" if cap == 0 else str(max(cap - t_sold, 0))
            lines.append(f"  • #{t.get('id')} {t.get('name')} — "
                         f"{_money(float(t.get('price') or 0))} each, "
                         f"capacity {cap if cap else 'unlimited'}, "
                         f"{t_sold} sold, {t_checked} checked in, "
                         f"{left} left"
                         f"{' — ' + str(t.get('description') or '') if t.get('description') else ''}")
        return "\n".join(lines)
    if mode == "add":
        clean_name = _clean(name, _MAX_NAME_CHARS)
        if not clean_name:
            return "That needs the ticket type's name."
        clean_price = _coerce_float(price)
        if clean_price is None or clean_price < 0 or clean_price > _MAX_PRICE:
            return ("The ticket price needs to be zero or more — a "
                    "recorded quote, never a payment. Nothing was "
                    "added.")
        clean_cap = _coerce_int(capacity)
        if clean_cap is None or clean_cap < 0 or clean_cap > _MAX_CAPACITY:
            return ("The capacity needs to be zero or more (zero "
                    "means unlimited). Nothing was added.")
        clean_desc = _clean(description, _MAX_DESC_CHARS)
        if rec is None:
            rec = _new_ticketing(clean_plan)
        if len(rec.get("types") or []) >= _MAX_TYPES:
            return (f"'{clean_plan}' already holds {_MAX_TYPES} ticket "
                    f"types — the cap. Nothing was added.")
        for t in (rec.get("types") or []):
            if str(t.get("name") or "").strip().lower() == clean_name.lower():
                return (f"There is already a ticket type called "
                        f"'{clean_name}' on '{clean_plan}'. Pick a "
                        f"different name.{broken_note}")
        new_id = int(rec.get("next_type_id") or 1)
        rec["next_type_id"] = new_id + 1
        rec.setdefault("types", []).append({
            "id": new_id, "name": clean_name, "price": clean_price,
            "capacity": clean_cap, "description": clean_desc,
            "created": time.time()})
        if not _save_ticketing(rec):
            return (f"Couldn't save the ticket type '{clean_name}' — "
                    f"the write did not verify. Nothing was added.")
        reread, _ = _load_ticketing()
        back = _find_ticketing(reread, clean_plan)
        if back is None or not any(
                int(t.get("id") or 0) == new_id
                for t in (back.get("types") or [])):
            return (f"The ticket type was written but the re-read "
                    f"could not find it on '{clean_plan}' — treat it "
                    f"as not added.")
        ledger = _get_ledger()
        try:
            note = _ledger_note(ledger, "type_add", {
                "plan": clean_plan, "type_id": new_id,
                "name": clean_name, "price": clean_price,
                "capacity": clean_cap})
        finally:
            ledger.close()
        return (f"Added ticket type #{new_id} '{clean_name}' on "
                f"'{clean_plan}' — {_money(clean_price)} each, "
                f"capacity {'unlimited' if clean_cap == 0 else clean_cap}."
                f"{note}{broken_note}")
    wanted = _coerce_int(type_id)
    if wanted is None or wanted < 1:
        return "That needs the ticket type's number (type_id)."
    if rec is None:
        return (f"No ticket types on '{clean_plan}' yet."
                f"{broken_note}")
    target = next((t for t in (rec.get("types") or [])
                   if int(t.get("id") or 0) == wanted), None)
    if target is None:
        return (f"No ticket type #{wanted} on '{clean_plan}'."
                f"{broken_note}")
    if mode == "get":
        t_sold, t_checked = _type_counts(rec, wanted)
        cap = int(target.get("capacity") or 0)
        left = "unlimited" if cap == 0 else str(max(cap - t_sold, 0))
        return (f"Ticket type #{wanted} '{target.get('name')}' on "
                f"'{clean_plan}' — {_money(float(target.get('price') or 0))} each, "
                f"capacity {cap if cap else 'unlimited'}, {t_sold} sold, "
                f"{t_checked} checked in, {left} left"
                f"{' — ' + str(target.get('description') or '') if target.get('description') else ''}."
                f"{broken_note}")
    # mode == "remove"
    t_sold, _ = _type_counts(rec, wanted)
    if t_sold:
        return (f"Can't remove ticket type #{wanted} "
                f"'{target.get('name')}' — {t_sold} ticket(s) were "
                f"already sold on it. Void those sales first, or "
                f"leave the type in place.{broken_note}")
    rec["types"] = [t for t in (rec.get("types") or [])
                    if int(t.get("id") or 0) != wanted]
    if not _save_ticketing(rec):
        return (f"Couldn't remove ticket type #{wanted} — the write "
                f"did not verify. Nothing was removed.")
    reread, _ = _load_ticketing()
    back = _find_ticketing(reread, clean_plan)
    if back is not None and any(
            int(t.get("id") or 0) == wanted
            for t in (back.get("types") or [])):
        return (f"The ticket type was removed but the re-read still "
                f"finds it on '{clean_plan}' — treat it as not "
                f"removed.")
    ledger = _get_ledger()
    try:
        note = _ledger_note(ledger, "type_remove", {
            "plan": clean_plan, "type_id": wanted,
            "name": str(target.get("name") or "")})
    finally:
        ledger.close()
    return (f"Removed ticket type #{wanted} "
            f"'{target.get('name')}' from '{clean_plan}'."
            f"{note}{broken_note}")


def _sale_action(mode, rec, clean_plan, type_id, sale_id, buyer,
                 qty, pay_method, pay_ref, broken_note) -> str:
    if mode == "list":
        if rec is None or not (rec.get("sales") or []):
            return (f"No ticket sales on '{clean_plan}' yet — record "
                    f"one with action 'sale', mode 'add'.{broken_note}")
        lines = [f"Ticket sales on '{clean_plan}':{broken_note}"]
        for s in (rec.get("sales") or []):
            tickets = s.get("tickets") or []
            checked = sum(1 for t in tickets if t.get("checked_in"))
            lines.append(f"  • sale #{s.get('id')} — "
                         f"{len(tickets)} × '{s.get('type_name')}' to "
                         f"{s.get('buyer')}, recorded "
                         f"{_money(float(s.get('total') or 0))} "
                         f"({s.get('pay_method') or 'not recorded'}"
                         f"{', ref ' + str(s.get('pay_ref') or '') if s.get('pay_ref') else ''}), "
                         f"{checked}/{len(tickets)} checked in"
                         f"{' — VOID' if s.get('void') else ''}")
        return "\n".join(lines)
    if mode == "add":
        wanted = _coerce_int(type_id)
        if wanted is None or wanted < 1:
            return "That needs the ticket type's number (type_id)."
        clean_buyer = _clean(buyer, _MAX_BUYER_CHARS)
        if not clean_buyer:
            return "That needs the buyer's name."
        clean_qty = _coerce_int(qty)
        if clean_qty is None or clean_qty < 1 or clean_qty > _MAX_QTY:
            return (f"The quantity needs to be between 1 and "
                    f"{_MAX_QTY}. Nothing was recorded.")
        clean_method = _clean(pay_method, _MAX_PAY_METHOD_CHARS) or "not recorded"
        clean_ref = _clean(pay_ref, _MAX_PAY_REF_CHARS)
        if rec is None:
            return (f"No ticket types on '{clean_plan}' yet — add one "
                    f"first with action 'type', mode 'add'."
                    f"{broken_note}")
        target = next((t for t in (rec.get("types") or [])
                       if int(t.get("id") or 0) == wanted), None)
        if target is None:
            return (f"No ticket type #{wanted} on '{clean_plan}'."
                    f"{broken_note}")
        if len(rec.get("sales") or []) >= _MAX_SALES:
            return (f"'{clean_plan}' already holds {_MAX_SALES} sale "
                    f"records — the cap. Nothing was recorded.")
        sold, _ = _type_counts(rec, wanted)
        cap = int(target.get("capacity") or 0)
        if cap and sold + clean_qty > cap:
            return (f"'{target.get('name')}' holds {cap} and {sold} "
                    f"are already sold — {max(cap - sold, 0)} left, "
                    f"so {clean_qty} more won't fit. Nothing was "
                    f"recorded.")
        unit_price = float(target.get("price") or 0)
        new_id = int(rec.get("next_sale_id") or 1)
        rec["next_sale_id"] = new_id + 1
        tickets = []
        qr_missing = 0
        for _ in range(clean_qty):
            seq = int(rec.get("next_code") or 1)
            rec["next_code"] = seq + 1
            code = _ticket_code(clean_plan, seq)
            qr_path = _render_qr(code, clean_plan)
            if qr_path is None:
                qr_missing += 1
            tickets.append({"code": code, "qr": qr_path,
                            "checked_in": None})
        rec.setdefault("sales", []).append({
            "id": new_id, "type_id": wanted,
            "type_name": str(target.get("name") or ""),
            "buyer": clean_buyer, "qty": clean_qty,
            "unit_price": unit_price, "total": unit_price * clean_qty,
            "pay_method": clean_method, "pay_ref": clean_ref,
            "issued": time.time(), "void": False, "void_note": "",
            "tickets": tickets})
        if not _save_ticketing(rec):
            return (f"Couldn't record the sale — the write did not "
                    f"verify. Nothing was recorded.")
        reread, _ = _load_ticketing()
        back = _find_ticketing(reread, clean_plan)
        if back is None or not any(
                int(s.get("id") or 0) == new_id
                for s in (back.get("sales") or [])):
            return (f"The sale was written but the re-read could not "
                    f"find it on '{clean_plan}' — treat it as not "
                    f"recorded.")
        ledger = _get_ledger()
        try:
            note = _ledger_note(ledger, "sale_add", {
                "plan": clean_plan, "sale_id": new_id,
                "type_id": wanted, "buyer": clean_buyer,
                "qty": clean_qty, "total": unit_price * clean_qty})
        finally:
            ledger.close()
        codes = ", ".join(t["code"] for t in tickets)
        qr_note = ""
        if qr_missing:
            qr_note = (f" ({qr_missing} QR image(s) could not be "
                       f"rendered — the codes below still check in.)")
        return (f"Recorded sale #{new_id} on '{clean_plan}': "
                f"{clean_qty} × '{target.get('name')}' to "
                f"{clean_buyer}, recorded "
                f"{_money(unit_price * clean_qty)} "
                f"({clean_method}"
                f"{', ref ' + clean_ref if clean_ref else ''}) — a "
                f"record, not a payment; the money moved outside "
                f"VYUK. Tickets: {codes}.{qr_note}{note}{broken_note}")
    wanted = _coerce_int(sale_id)
    if wanted is None or wanted < 1:
        return "That needs the sale's number (sale_id)."
    if rec is None:
        return (f"No ticket sales on '{clean_plan}' yet."
                f"{broken_note}")
    target = next((s for s in (rec.get("sales") or [])
                   if int(s.get("id") or 0) == wanted), None)
    if target is None:
        return (f"No sale #{wanted} on '{clean_plan}'."
                f"{broken_note}")
    if mode == "get":
        tickets = target.get("tickets") or []
        lines = [f"Sale #{wanted} on '{clean_plan}': "
                 f"{len(tickets)} × '{target.get('type_name')}' to "
                 f"{target.get('buyer')}, recorded "
                 f"{_money(float(target.get('total') or 0))} "
                 f"({target.get('pay_method') or 'not recorded'}"
                 f"{', ref ' + str(target.get('pay_ref') or '') if target.get('pay_ref') else ''}), "
                 f"issued {_fmt_time(target.get('issued'))}"
                 f"{' — VOID' + (' (' + str(target.get('void_note') or '') + ')' if target.get('void_note') else '') if target.get('void') else ''}."
                 f"{broken_note}"]
        for t in tickets:
            lines.append(f"  • {t.get('code')} — "
                         f"{'checked in ' + _fmt_time(t.get('checked_in')) if t.get('checked_in') else 'not checked in yet'}"
                         f"{' — QR: ' + str(t.get('qr') or '') if t.get('qr') else ''}")
        return "\n".join(lines)
    # mode == "remove" — void the sale record (the tickets stop
    # working; any refund was handled outside VYUK and is only
    # noted here, never moved by the tool).
    if target.get("void"):
        return (f"Sale #{wanted} on '{clean_plan}' is already void."
                f"{broken_note}")
    target["void"] = True
    target["void_note"] = ("voided by the owner — any refund was "
                           "handled outside VYUK")
    if not _save_ticketing(rec):
        target["void"] = False
        target["void_note"] = ""
        return (f"Couldn't void sale #{wanted} — the write did not "
                f"verify. Nothing was voided.")
    reread, _ = _load_ticketing()
    back = _find_ticketing(reread, clean_plan)
    back_sale = None
    if back is not None:
        back_sale = next((s for s in (back.get("sales") or [])
                          if int(s.get("id") or 0) == wanted), None)
    if back_sale is None or not back_sale.get("void"):
        return (f"The sale was voided but the re-read still shows "
                f"it live on '{clean_plan}' — treat it as not "
                f"voided.")
    ledger = _get_ledger()
    try:
        note = _ledger_note(ledger, "sale_void", {
            "plan": clean_plan, "sale_id": wanted,
            "buyer": str(target.get("buyer") or "")})
    finally:
        ledger.close()
    return (f"Voided sale #{wanted} on '{clean_plan}' — its "
            f"{len(target.get('tickets') or [])} ticket(s) can no "
            f"longer check in. Any refund was handled outside "
            f"VYUK.{note}{broken_note}")


def _checkin_action(mode, rec, clean_plan, code, broken_note) -> str:
    if mode not in _CHECKIN_MODES:
        return ("Check-in modes are 'checkin' (check one ticket in "
                "by code) and 'list'.")
    if rec is None or not (rec.get("sales") or []):
        return (f"No ticket sales on '{clean_plan}' yet — record a "
                f"sale first with action 'sale', mode 'add'."
                f"{broken_note}")
    if mode == "list":
        checked_lines = []
        waiting_lines = []
        for s in (rec.get("sales") or []):
            if s.get("void"):
                continue
            for t in (s.get("tickets") or []):
                entry = (f"  • {t.get('code')} — "
                         f"{s.get('buyer')} "
                         f"('{s.get('type_name')}')")
                if t.get("checked_in"):
                    checked_lines.append(
                        entry + " — in at " + _fmt_time(t.get("checked_in")))
                else:
                    waiting_lines.append(entry)
        lines = [f"Check-in on '{clean_plan}': {len(checked_lines)} "
                 f"in, {len(waiting_lines)} still to arrive."
                 f"{broken_note}"]
        if checked_lines:
            lines.append("Checked in:")
            lines += checked_lines
        if waiting_lines:
            lines.append("Still to arrive:")
            lines += waiting_lines
        return "\n".join(lines)
    clean_code = _clean(code, _MAX_CODE_CHARS).upper()
    if not clean_code:
        return "That needs the ticket's code (from its QR or the sale record)."
    sale, ticket = _find_ticket(rec, clean_code)
    if sale is None or ticket is None:
        return (f"No ticket with the code '{clean_code}' on "
                f"'{clean_plan}'. Check the code and try again.")
    if sale.get("void"):
        return (f"Ticket '{clean_code}' belongs to sale "
                f"#{sale.get('id')}, which was voided — it can no "
                f"longer check in.")
    if ticket.get("checked_in"):
        return (f"Ticket '{clean_code}' already checked in at "
                f"{_fmt_time(ticket.get('checked_in'))} "
                f"({sale.get('buyer')}, '{sale.get('type_name')}').")
    ticket["checked_in"] = time.time()
    if not _save_ticketing(rec):
        ticket["checked_in"] = None
        return (f"Couldn't record the check-in — the write did not "
                f"verify. Treat it as not checked in.")
    reread, _ = _load_ticketing()
    back = _find_ticketing(reread, clean_plan)
    back_sale, back_ticket = _find_ticket(back, clean_code) if back else (None, None)
    if back_ticket is None or not back_ticket.get("checked_in"):
        return (f"The check-in was written but the re-read could "
                f"not find it on '{clean_plan}' — treat it as not "
                f"checked in.")
    ledger = _get_ledger()
    try:
        note = _ledger_note(ledger, "checkin", {
            "plan": clean_plan, "code": clean_code,
            "buyer": str(sale.get("buyer") or ""),
            "type": str(sale.get("type_name") or "")})
    finally:
        ledger.close()
    return (f"Checked in ticket '{clean_code}' — "
            f"{sale.get('buyer')} ('{sale.get('type_name')}') at "
            f"{_fmt_time(ticket.get('checked_in'))}."
            f"{note}{broken_note}")


# ---------------------------------------------------------------------------
# Registration — the module owns its catalogue via register_all
# (same pattern as the other packs)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register the event-ticketing pack (R18-7, Attendize
    inspiration): event_tickets — ticket types, sale records
    and QR check-in for one event plan (free by design,
    "app-state" — it writes only to VYUK's own ticketing
    store, the event_plan precedent). Own stdlib code over
    the tree's own money + event-planning + QR shapes; nothing
    adopted — no Laravel, no hosted site, no payment gateway,
    no multi-organiser accounts. The module owns its catalogue
    via register_all (same pattern as the other packs)."""
    registry.register(
        name="event_tickets",
        description=(
            "Ticket types, sale records and QR check-in for an event "
            "plan: add ticket types (name, recorded price, capacity), "
            "record a sale (buyer, quantity, how they paid — noted as "
            "a record, never a payment), get a QR code per ticket, and "
            "check attendees in by ticket code. A sale is a record the "
            "owner made; the money moved outside VYUK. Use when the user "
            "sells or tracks tickets for an event they planned."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "One of: 'type' (ticket types), 'sale' (sale records), 'checkin' (check attendees in), 'status' (counts) or 'events' (the ledger)"},
                "mode": {"type": "string",
                         "description": "With 'type'/'sale', one of 'add', 'list', 'get' or 'remove' (sale remove voids the record); with 'checkin', 'checkin' or 'list'"},
                "plan": {"type": "string",
                         "description": "The event plan's name, e.g. 'Spring wedding'"},
                "type_id": {"type": "integer",
                            "description": "With 'type' get/remove or 'sale' add, the ticket type's number"},
                "sale_id": {"type": "integer",
                            "description": "With 'sale' get/remove, the sale's number"},
                "name": {"type": "string",
                         "description": "With 'type' add, the ticket type's name (at most 200 chars)"},
                "price": {"type": "number",
                         "description": "With 'type' add, the ticket's recorded price (zero or more; a quote, never a payment)"},
                "capacity": {"type": "integer",
                             "description": "With 'type' add, how many tickets of this type may be sold (zero means unlimited)"},
                "description": {"type": "string",
                                "description": "With 'type' add, a short description of the ticket type (at most 500 chars)"},
                "buyer": {"type": "string",
                          "description": "With 'sale' add, the buyer's name"},
                "qty": {"type": "integer",
                        "description": "With 'sale' add, how many tickets (1 to 100)"},
                "pay_method": {"type": "string",
                               "description": "With 'sale' add, how the buyer paid, noted as a record (e.g. 'cash', 'bank transfer', 'at the door')"},
                "pay_ref": {"type": "string",
                            "description": "With 'sale' add, an optional payment reference the owner types in"},
                "code": {"type": "string",
                         "description": "With 'checkin', the ticket's code (from its QR or the sale record)"},
                "limit": {"type": "integer",
                          "description": "With 'events', most events shown (20 if left out, max 100)"},
            },
            "required": ["action", "plan"],
        },
        handler=event_tickets,
    )
