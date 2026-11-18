"""An invoice generator (R19-5, al1abb/invoify inspiration).

Invoify is a web app that generates invoices — create invoices with
line items, templates, PDF export, and (in some versions) email
sending. This module builds the honest buildable remainder in own
stdlib code: INVOICE GENERATION — render an erp invoice as a PDF
(three templates), and send it by email with the PDF attached.
The invoice RECORD itself is not rebuilt here — erp (R19-3) already
keeps it. This tool reads the erp invoice, renders it, and sends it.

Licence: MIT — "Parent-ruled (licence, 2026-11-12)" (the Invoify LICENSE, read
in full from master: 1,068 bytes, 21 lines, "Copyright (c) 2023 Ali
Abbasov"; no code adopted, name not used).
Nothing is adopted — no Next.js, no TypeScript, no Shadcn, no web
app, no wrapper, no port. The build takes only the invoice shapes
(render a PDF, templates, send by email) and rebuilds them as own
stdlib code.

What is built (one effectful tool `invoice`, confirm-gated for send):
  * render — render an erp invoice as a PDF (three templates:
    classic, modern, minimal), written to an operator-supplied path
    or to the default renders folder. The PDF is written by a
    minimal stdlib PDF writer in this module — no PDF library, no
    new dependency. The line items come from the sales order (for an
    order-based invoice) or from operator-supplied lines (for a
    standalone invoice, whose lines are not stored by erp).
  * template — list the built-in templates / get one template's
    description.
  * seller — set / get the business (seller) details once (name,
    address, email, phone); render and send use them.
  * send — render the PDF and send it as an attachment via the
    product's own Gmail (the send_email tool, extended in this batch
    with an optional `attachments` property). Confirm-gated (C-14
    approve-first): this SENDS real mail.
  * list / get — the render and send records.
  * status — counts of renders, sends, templates, ledger state.
  * events — the hash-chained ledger (list / verify).

What is NOT built (STOP, stated on the tin):
  * NO WEB APP, no Next.js, no hosted anything, no public page.
    The tool is local only, single-user by design.
  * NO invoice record — erp already keeps invoices (create/list/
    get/void); this tool renders and sends them, it does not
    re-create them.
  * NO tax computation, no tax advice, no tax filing. The totals
    are the erp invoice totals (line sums only) — this tool never
    computes tax.
  * NO money movement of any kind: no payment capture, no Stripe
    calls, no bank transfer, no live keys. Sending an invoice is
    sending a document, not moving money.
  * NO PDF library, no new dependency. The PDF is written by a
    minimal stdlib writer (own code); the PDF is verified by a
    stdlib-only xref parser (no pypdf — pypdf is optional, commented
    out in requirements.txt, and not installed on sys).
  * NO multi-user roles, no staff accounts, no channel sync, no
    network call beyond the Gmail send itself.

Classification: `send` calls the owner's Gmail endpoint and SENDS
real mail, so it is confirm-gated (C-14 approve-first). `render`,
`template`, `seller`, `list`, `get`, `status` and `events` are local
app-state and reads — those stay free. Unknown or missing action
gates fail-closed (the handler refuses them anyway).

Bounds: invoice lines at most 100; each line quantity at most
100,000; each amount at most 1,000,000,000 minor units (the erp
bounds); seller name / address / email / phone at most 120 chars
each; the PDF output path at most 500 chars; attachments at most 5
files, each at most 25 MB (Gmail's own ceiling); the rendered PDF
at most 20 pages (an invoice with more than ~900 lines is refused
with the numbers).
"""

import hashlib
import json
import re
import sqlite3
import time
from datetime import date
from pathlib import Path

from .config import DATA_DIR
from . import store_base

_ACTIONS = ("render", "template", "seller", "send", "list", "get",
            "status", "events")

_TEMPLATES = ("classic", "modern", "minimal")

_MAX_LINES = 100
_MAX_QTY = 100000
_MAX_AMOUNT = 1000000000
_MAX_SELLER = 120
_MAX_PATH = 500
_MAX_ATTACHMENTS = 5
_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
_MAX_PDF_PAGES = 20
_MAX_LIMIT = 100
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _Refusal(Exception):
    """A refusal with the numbers — never a silent wrong answer."""


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _clean_str(v, what, limit=_MAX_SELLER, required=True):
    if v is None:
        if required:
            raise _Refusal("%s is required" % what)
        return None
    if not isinstance(v, str) or not v.strip():
        if required:
            raise _Refusal("%s must be a non-empty string" % what)
        return None
    v = v.strip()
    if len(v) > limit:
        raise _Refusal("%s must be at most %d chars" % (what, limit))
    return v


def _clean_int(v, what, minimum=0, maximum=_MAX_AMOUNT):
    if v is None:
        raise _Refusal("%s is required" % what)
    if not _is_int(v) or v < minimum or v > maximum:
        raise _Refusal("%s must be a whole number between %d and %d"
                       % (what, minimum, maximum))
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


def _fmt_money(cents):
    """Integer minor units -> major units with two decimals."""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return "%s%d.%02d" % (sign, cents // 100, cents % 100)


# ---------------------------------------------------------------------------
# Minimal stdlib PDF writer (own code — no PDF library, no new dependency)
# ---------------------------------------------------------------------------
#
# A PDF is a text-based format: a header, numbered objects, a cross-
# reference table with byte-exact object offsets, and a trailer. The
# writer below emits exactly that: a catalog, a pages tree, one
# Helvetica base-14 font (no embedding needed), one page object and
# one content stream per page. The content stream uses only the
# operators this module needs: BT/ET + Tf/Td/Tj for text, m/l/S for
# rules, re/f for a filled header bar. Every emitted PDF is verified
# by _verify_pdf_bytes (a stdlib-only xref parser) before the render
# is claimed.

_PAGE_W = 612.0
_PAGE_H = 792.0
_MARGIN = 54.0


def _pdf_escape(text):
    """Escape a string for a PDF literal and encode it latin-1."""
    out = []
    for ch in str(text):
        if ch == "\\":
            out.append("\\\\")
        elif ch == "(":
            out.append("\\(")
        elif ch == ")":
            out.append("\\)")
        elif ord(ch) < 32 or ord(ch) > 126:
            # latin-1 printable range; anything else becomes '?'
            out.append(ch if 32 <= ord(ch) <= 255 else "?")
        else:
            out.append(ch)
    return "".join(out).encode("latin-1", "replace")


def _pdf_content_stream(elements):
    """Build one page's content stream from draw elements.

    Each element is a dict:
      {"type": "text", "x": f, "y": f, "size": f, "text": str,
       "bold": bool, "color": (r, g, b) or None}
      {"type": "rule", "x1": f, "y1": f, "x2": f, "y2": f,
       "width": f, "color": (r, g, b) or None}
      {"type": "rect", "x": f, "y": f, "w": f, "h": f,
       "fill": (r, g, b) or None}
    Helvetica is the only font (F1); bold is faked by drawing the
    text twice with a 0.4 pt offset (a base-14 font has no bold
    variant to embed).
    """
    parts = []
    for el in elements:
        kind = el.get("type")
        if kind == "text":
            color = el.get("color")
            if color:
                parts.append("%.3f %.3f %.3f rg" % color)
            parts.append("BT /F1 %.1f Tf %.1f %.1f Td (%s) Tj ET"
                         % (el["size"], el["x"], el["y"],
                            _pdf_escape(el["text"]).decode("latin-1")))
            if el.get("bold"):
                parts.append("BT /F1 %.1f Tf %.1f %.1f Td (%s) Tj ET"
                             % (el["size"], el["x"] + 0.4, el["y"],
                                _pdf_escape(el["text"]).decode("latin-1")))
            if color:
                parts.append("0 0 0 rg")
        elif kind == "rule":
            color = el.get("color") or (0, 0, 0)
            parts.append("%.3f %.3f %.3f RG %.1f w %.1f %.1f m %.1f %.1f l S"
                         % (color[0], color[1], color[2],
                            el.get("width", 0.7),
                            el["x1"], el["y1"], el["x2"], el["y2"]))
            parts.append("0 0 0 RG")
        elif kind == "rect":
            fill = el.get("fill") or (0, 0, 0)
            parts.append("%.3f %.3f %.3f rg %.1f %.1f %.1f %.1f re f"
                         % (fill[0], fill[1], fill[2],
                            el["x"], el["y"], el["w"], el["h"]))
            parts.append("0 0 0 rg")
    return ("\n".join(parts) + "\n").encode("latin-1")


def _write_pdf(path, pages):
    """Write a PDF file from a list of pages (each a list of draw
    elements). Returns (sha256_hex, page_count). Raises _Refusal on
    any write failure, with the reason."""
    if not pages:
        raise _Refusal("cannot write a PDF with no pages")
    if len(pages) > _MAX_PDF_PAGES:
        raise _Refusal("the invoice needs %d pages — at most %d"
                       % (len(pages), _MAX_PDF_PAGES))
    objects = []  # (number, body_bytes)
    # 1: catalog, 2: pages tree, 3: Helvetica font.
    page_numbers = [4 + 2 * i for i in range(len(pages))]
    content_numbers = [5 + 2 * i for i in range(len(pages))]
    kids = " ".join("%d 0 R" % n for n in page_numbers)
    objects.append((1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    objects.append((2, (b"<< /Type /Pages /Kids [%s] /Count %d >>"
                        % (kids.encode("ascii"), len(pages)))))
    objects.append((3, b"<< /Type /Font /Subtype /Type1 "
                       b"/BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"))
    for i, elements in enumerate(pages):
        stream = _pdf_content_stream(elements)
        objects.append((page_numbers[i],
                        (b"<< /Type /Page /Parent 2 0 R "
                         b"/MediaBox [0 0 612 792] "
                         b"/Resources << /Font << /F1 3 0 R >> >> "
                         b"/Contents %d 0 R >>" % content_numbers[i])))
        objects.append((content_numbers[i],
                        (b"<< /Length %d >>\nstream\n" % len(stream)
                         + stream + b"endstream")))
    # Assemble: header, objects (in number order), xref, trailer.
    out = bytearray()
    out += b"%PDF-1.4\n"
    offsets = {}
    for number, body in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref_pos = len(out)
    total = max(offsets) + 1
    out += b"xref\n0 %d\n" % total
    out += b"0000000000 65535 f \n"
    for number in range(1, total):
        out += b"%010d 00000 n \n" % offsets[number]
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (total, xref_pos))
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(bytes(out))
    except OSError as exc:
        raise _Refusal("could not write the PDF to %s (%s)" % (path, exc))
    return hashlib.sha256(bytes(out)).hexdigest(), len(pages)


def _verify_pdf_bytes(data):
    """Verify a PDF's structure with a stdlib-only xref parser.
    Returns (is_valid, page_count, error). No pypdf needed."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 20:
        return False, 0, "too short to be a PDF"
    if not bytes(data).startswith(b"%PDF-"):
        return False, 0, "missing the %PDF- header"
    try:
        text = bytes(data)
        # The xref table: find "xref", parse the subsection, verify
        # every object's offset points at "N 0 obj".
        xref_at = text.rfind(b"\nxref\n")
        if xref_at < 0:
            xref_at = text.find(b"xref\n")
        if xref_at < 0:
            return False, 0, "no xref table"
        after = text[xref_at + 5:].split(b"\n")
        if len(after) < 2 or not after[1].strip().split():
            return False, 0, "malformed xref subsection"
        first, count = after[1].strip().split()[:2]
        first, count = int(first), int(count)
        entries = []
        for line in after[2:2 + count]:
            parts = line.strip().split()
            # An xref entry is "offset generation kind" — e.g.
            # "0000000000 65535 f" (free) or "0000000015 00000 n".
            if len(parts) < 3:
                return False, 0, "malformed xref entry"
            entries.append((int(parts[0]), parts[2]))
        if entries[0][1] != b"f":
            return False, 0, "the free xref entry is not free"
        for number in range(1, len(entries)):
            offset, kind = entries[number]
            if kind != b"n":
                continue
            marker = b"%d 0 obj" % number
            if text[offset:offset + len(marker)] != marker:
                return False, 0, ("xref offset for object %d does not "
                                  "point at its object" % number)
        # The trailer must name a /Root and a /Size.
        trailer_at = text.rfind(b"trailer")
        if trailer_at < 0 or b"/Root" not in text[trailer_at:]:
            return False, 0, "no /Root in the trailer"
        if b"/Size" not in text[trailer_at:]:
            return False, 0, "no /Size in the trailer"
        # Count the pages (one /Type /Page per page object).
        pages = len(re.findall(rb"/Type\s*/Page[^s]", text))
        if pages < 1:
            return False, 0, "no page object"
        return True, pages, ""
    except (ValueError, IndexError) as exc:
        return False, 0, "xref parse failed (%s)" % exc


# ---------------------------------------------------------------------------
# Store: one SQLite file (DATA_DIR) — renders + sends + seller + ledger
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    return Path(DATA_DIR) / "invoice.sqlite3"


def _erp_db_path() -> Path:
    return Path(DATA_DIR) / "erp.sqlite3"


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS renders ("
    " id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL,"
    " template TEXT NOT NULL, output_path TEXT NOT NULL,"
    " pdf_sha256 TEXT NOT NULL, pdf_pages INTEGER NOT NULL,"
    " created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sends ("
    " id TEXT PRIMARY KEY, render_id TEXT NOT NULL,"
    " invoice_id TEXT NOT NULL, recipient TEXT NOT NULL,"
    " gmail_message_id TEXT, created REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS seller ("
    " id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT,"
    " email TEXT, phone TEXT, updated REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS events ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
    " kind TEXT NOT NULL, payload TEXT NOT NULL,"
    " prev_hash TEXT NOT NULL, hash TEXT NOT NULL)",
)


class _InvoiceStore(store_base.StoreBase):
    """Renders + sends + seller + a hash-chained ledger. Open-per-
    operation; every write is followed by a re-read before the caller
    claims it (A1-store). Reads answer empty WITHOUT creating the
    file."""

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
            (ts, kind, json.dumps(payload), prev_hash, digest))
        return digest

    def append_event(self, kind, payload):
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
                    "SELECT id, ts, kind, payload, prev_hash, hash"
                    " FROM events ORDER BY id DESC LIMIT ?",
                    (limit,)).fetchall()
            finally:
                conn.close()
        return rows

    def verify_chain(self):
        if self._missing():
            return True, 0
        with _LOCK:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT ts, kind, payload, prev_hash, hash"
                    " FROM events ORDER BY id").fetchall()
            finally:
                conn.close()
        entries = [{"ts": ts, "kind": kind,
                    "payload": json.loads(payload),
                    "prev_hash": prev_hash, "hash": digest}
                   for ts, kind, payload, prev_hash, digest in rows]
        result = store_base.verify_entries(
            entries, "json_body_ts", store_base.GENESIS)
        return result["ok"], len(rows)

    def count(self, table, where="", args=()):
        if self._missing():
            return 0
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM %s %s" % (table, where),
                    args).fetchone()
            finally:
                conn.close()
        return row[0] if row else 0

    def get_row(self, table, rid):
        if self._missing():
            return None
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM %s WHERE id = ?" % table,
                    (rid,)).fetchone()
            finally:
                conn.close()
        return row

    def next_id(self, conn, prefix):
        row = conn.execute(
            "SELECT id FROM renders WHERE id LIKE ?"
            " ORDER BY id DESC LIMIT 1", (prefix + "-%",)).fetchone()
        n = int(row[0].split("-")[1]) + 1 if row else 1
        return "%s-%04d" % (prefix, n)


_LOCK = __import__("threading").Lock()


def _last_event_payload(conn):
    row = conn.execute(
        "SELECT payload FROM events ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else None


# ---------------------------------------------------------------------------
# Reading the erp invoice (read-only — this tool never writes erp's store)
# ---------------------------------------------------------------------------

def _read_erp_invoice(invoice_id):
    """Read an erp invoice + its customer + its line items, from the
    erp store (read-only). Returns a dict with keys: id, customer
    (dict), order_id, total_cents, paid_cents, status, due_date,
    note, lines (a list of {description, quantity, unit_cents,
    line_total}), lines_from ("order" or "none"). Raises _Refusal
    when the invoice does not exist or the erp store is missing."""
    erp_path = _erp_db_path()
    if not erp_path.exists():
        raise _Refusal("no erp store yet — create an invoice with"
                       " the erp tool first")
    conn = store_base.connect(str(erp_path))
    try:
        row = conn.execute(
            "SELECT id, customer_id, order_id, total_cents,"
            " paid_cents, status, due_date, note, created"
            " FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if row is None:
            raise _Refusal("no invoice with id %s" % invoice_id)
        (iid, cid, order_id, total, paid, status, due,
         note, created) = row
        crow = conn.execute(
            "SELECT id, name, email, phone, company"
            " FROM customers WHERE id = ?", (cid,)).fetchone()
        if crow is None:
            raise _Refusal("the invoice's customer %s is missing"
                           % cid)
        customer = {"id": crow[0], "name": crow[1], "email": crow[2],
                    "phone": crow[3], "company": crow[4]}
        lines = []
        lines_from = "none"
        if order_id:
            orows = conn.execute(
                "SELECT product_id, quantity, price_cents"
                " FROM sales_order_lines WHERE order_id = ?"
                " ORDER BY line_no", (order_id,)).fetchall()
            for pid, qty, price in orows:
                prow = conn.execute(
                    "SELECT name FROM products WHERE id = ?",
                    (pid,)).fetchone()
                name = prow[0] if prow else pid
                lines.append({"description": name, "quantity": qty,
                              "unit_cents": price,
                              "line_total": qty * price})
            lines_from = "order"
    finally:
        conn.close()
    return {"id": iid, "customer": customer, "order_id": order_id,
            "total_cents": total, "paid_cents": paid,
            "status": status, "due_date": due, "note": note,
            "created": created, "lines": lines,
            "lines_from": lines_from}


def _parse_render_lines(lines, store_erp_conn=None):
    """Validate operator-supplied lines for a standalone invoice.
    Each line is {description, quantity, unit_price_cents} or
    {product_id, quantity, price_cents} (the product name is looked
    up in the erp store). Returns [(description, qty, unit, total)]
    and the total. Raises _Refusal on any bad line."""
    if lines is None:
        return [], 0
    if not isinstance(lines, list) or not lines:
        raise _Refusal("lines must be a non-empty list")
    if len(lines) > _MAX_LINES:
        raise _Refusal("at most %d lines per invoice" % _MAX_LINES)
    out = []
    total = 0
    erp_path = _erp_db_path()
    conn = None
    try:
        for i, line in enumerate(lines):
            if not isinstance(line, dict):
                raise _Refusal("line %d must be an object" % (i + 1))
            qty = line.get("quantity")
            if not _is_int(qty) or qty < 1 or qty > _MAX_QTY:
                raise _Refusal("line %d quantity must be a whole"
                               " number between 1 and %d"
                               % (i + 1, _MAX_QTY))
            if "description" in line:
                desc = _clean_str(line.get("description"),
                                  "line %d description" % (i + 1),
                                  limit=200)
                unit = line.get("unit_price_cents")
                if not _is_int(unit) or unit < 0 or unit > _MAX_AMOUNT:
                    raise _Refusal("line %d unit_price_cents must be"
                                   " a whole number between 0 and %d"
                                   % (i + 1, _MAX_AMOUNT))
            elif "product_id" in line:
                pid = _clean_str(line.get("product_id"),
                                 "line %d product_id" % (i + 1),
                                 limit=100)
                unit = line.get("price_cents")
                if not _is_int(unit) or unit < 0 or unit > _MAX_AMOUNT:
                    raise _Refusal("line %d price_cents must be a"
                                   " whole number between 0 and %d"
                                   % (i + 1, _MAX_AMOUNT))
                if conn is None:
                    if not erp_path.exists():
                        raise _Refusal("no erp store yet — product"
                                       " lines need the erp tool")
                    conn = store_base.connect(str(erp_path))
                prow = conn.execute(
                    "SELECT name FROM products WHERE id = ?",
                    (pid,)).fetchone()
                if prow is None:
                    raise _Refusal("line %d: no product with id %s"
                                   % (i + 1, pid))
                desc = prow[0]
            else:
                raise _Refusal("line %d needs a description or a"
                               " product_id" % (i + 1))
            line_total = qty * unit
            if line_total > _MAX_AMOUNT:
                raise _Refusal("line %d total exceeds %d minor units"
                               % (i + 1, _MAX_AMOUNT))
            out.append((desc, qty, unit, line_total))
            total += line_total
    finally:
        if conn is not None:
            conn.close()
    return out, total


# ---------------------------------------------------------------------------
# Templates: three layouts, one content set
# ---------------------------------------------------------------------------

_TEMPLATES_DESC = {
    "classic": ("Classic — a formal invoice: a bold title, a thin"
                " rule under it, seller on the left and bill-to on"
                " the right, a line-items table, totals right-aligned,"
                " and a footer note. The default."),
    "modern": ("Modern — a dark header bar with the title in white,"
               " a larger title, and the totals in bold. Same"
               " content as classic, a bolder look."),
    "minimal": ("Minimal — a compact invoice: smaller type, tighter"
                " spacing, one thin rule, no decorative header. Same"
                " content, the least ink."),
}

_LAYOUT = {
    "classic": {"title_size": 22.0, "body_size": 11.0,
                "small_size": 9.0, "line_h": 16.0, "title_y": 740.0,
                "header_bar": False, "rule_w": 0.7},
    "modern": {"title_size": 26.0, "body_size": 11.0,
               "small_size": 9.0, "line_h": 16.0, "title_y": 736.0,
               "header_bar": True, "rule_w": 1.2},
    "minimal": {"title_size": 16.0, "body_size": 10.0,
                "small_size": 8.0, "line_h": 14.0, "title_y": 744.0,
                "header_bar": False, "rule_w": 0.4},
}

_DARK = (0.15, 0.15, 0.25)


def _layout_invoice(data, template, seller):
    """Lay out one invoice as a list of pages (each a list of draw
    elements). `data` is the dict from _read_erp_invoice (with the
    lines already resolved); `seller` is a dict (name, address,
    email, phone) or None. Paginates the line-items table."""
    lay = _LAYOUT[template]
    title_size = lay["title_size"]
    body = lay["body_size"]
    small = lay["small_size"]
    line_h = lay["line_h"]
    left = _MARGIN
    right = _PAGE_W - _MARGIN
    pages = []
    elements = []
    y = lay["title_y"]
    if lay["header_bar"]:
        elements.append({"type": "rect", "x": left - 6, "y": y - 6,
                         "w": right - left + 12, "h": title_size + 18,
                         "fill": _DARK})
        elements.append({"type": "text", "x": left, "y": y + 4,
                         "size": title_size, "text": "INVOICE",
                         "bold": True, "color": (1, 1, 1)})
        y -= title_size + 30
    else:
        elements.append({"type": "text", "x": left, "y": y,
                         "size": title_size, "text": "INVOICE",
                         "bold": True})
        y -= title_size + 10
        elements.append({"type": "rule", "x1": left, "y1": y,
                         "x2": right, "y2": y,
                         "width": lay["rule_w"]})
        y -= line_h + 4
    # Seller (left) and bill-to (right).
    if seller:
        elements.append({"type": "text", "x": left, "y": y,
                         "size": body, "text": seller.get("name") or "",
                         "bold": True})
        y -= line_h
        for field in ("address", "email", "phone"):
            value = seller.get(field)
            if value:
                elements.append({"type": "text", "x": left, "y": y,
                                 "size": small, "text": value})
                y -= line_h
        y -= 4
    cust = data["customer"]
    bill_lines = [cust["name"]]
    if cust.get("company"):
        bill_lines.append(cust["company"])
    if cust.get("email"):
        bill_lines.append(cust["email"])
    if cust.get("phone"):
        bill_lines.append(cust["phone"])
    by = lay["title_y"] - title_size - (30 if lay["header_bar"] else 14)
    if not lay["header_bar"]:
        by -= 4
    elements.append({"type": "text", "x": right - 180, "y": by,
                     "size": small, "text": "BILL TO", "bold": True})
    by -= line_h
    for line in bill_lines:
        elements.append({"type": "text", "x": right - 180, "y": by,
                         "size": small, "text": line})
        by -= line_h
    # Invoice meta.
    y = min(y, by) - line_h
    elements.append({"type": "text", "x": left, "y": y,
                     "size": body,
                     "text": "Invoice %s" % data["id"], "bold": True})
    y -= line_h
    if data.get("order_id"):
        elements.append({"type": "text", "x": left, "y": y,
                         "size": small,
                         "text": "Sales order: %s" % data["order_id"]})
        y -= line_h
    if data.get("due_date"):
        elements.append({"type": "text", "x": left, "y": y,
                         "size": small,
                         "text": "Due: %s" % data["due_date"]})
        y -= line_h
    elements.append({"type": "text", "x": left, "y": y,
                     "size": small,
                     "text": "Status: %s" % data["status"]})
    y -= line_h * 2
    # Line-items table header.
    col_desc = left
    col_qty = right - 180
    col_unit = right - 110
    col_amt = right - 60
    elements.append({"type": "text", "x": col_desc, "y": y,
                     "size": small, "text": "Description", "bold": True})
    elements.append({"type": "text", "x": col_qty, "y": y,
                     "size": small, "text": "Qty", "bold": True})
    elements.append({"type": "text", "x": col_unit, "y": y,
                     "size": small, "text": "Unit", "bold": True})
    elements.append({"type": "text", "x": col_amt, "y": y,
                     "size": small, "text": "Amount", "bold": True})
    y -= 4
    elements.append({"type": "rule", "x1": left, "y1": y,
                     "x2": right, "y2": y, "width": lay["rule_w"]})
    y -= line_h
    lines = data["lines"]
    if not lines:
        elements.append({"type": "text", "x": left, "y": y,
                         "size": small,
                         "text": ("(line items not stored for this"
                                  " invoice — supply lines to show"
                                  " them)")})
        y -= line_h
    for line in lines:
        desc = line["description"]
        qty = line["quantity"]
        unit = line["unit_cents"]
        line_total = line["line_total"]
        if y < _MARGIN + line_h * 6:
            pages.append(elements)
            elements = []
            y = _PAGE_H - _MARGIN
            elements.append({"type": "text", "x": col_desc, "y": y,
                             "size": small, "text": "Description",
                             "bold": True})
            elements.append({"type": "text", "x": col_qty, "y": y,
                             "size": small, "text": "Qty", "bold": True})
            elements.append({"type": "text", "x": col_unit, "y": y,
                             "size": small, "text": "Unit", "bold": True})
            elements.append({"type": "text", "x": col_amt, "y": y,
                             "size": small, "text": "Amount",
                             "bold": True})
            y -= line_h + 4
        elements.append({"type": "text", "x": col_desc, "y": y,
                         "size": small,
                         "text": desc[:60]})
        elements.append({"type": "text", "x": col_qty, "y": y,
                         "size": small, "text": str(qty)})
        elements.append({"type": "text", "x": col_unit, "y": y,
                         "size": small, "text": _fmt_money(unit)})
        elements.append({"type": "text", "x": col_amt, "y": y,
                         "size": small,
                         "text": _fmt_money(line_total)})
        y -= line_h
    y -= 4
    elements.append({"type": "rule", "x1": left, "y1": y,
                     "x2": right, "y2": y, "width": lay["rule_w"]})
    y -= line_h
    elements.append({"type": "text", "x": col_unit, "y": y,
                     "size": body, "text": "Total", "bold": True})
    elements.append({"type": "text", "x": col_amt, "y": y,
                     "size": body,
                     "text": _fmt_money(data["total_cents"]),
                     "bold": True})
    y -= line_h
    elements.append({"type": "text", "x": col_unit, "y": y,
                     "size": small, "text": "Paid"})
    elements.append({"type": "text", "x": col_amt, "y": y,
                     "size": small,
                     "text": _fmt_money(data["paid_cents"])})
    y -= line_h
    elements.append({"type": "text", "x": col_unit, "y": y,
                     "size": small, "text": "Balance"})
    elements.append({"type": "text", "x": col_amt, "y": y,
                     "size": small,
                     "text": _fmt_money(data["total_cents"]
                                        - data["paid_cents"])})
    y -= line_h * 2
    if data.get("note"):
        elements.append({"type": "text", "x": left, "y": y,
                         "size": small,
                         "text": "Note: %s" % data["note"][:120]})
        y -= line_h
    elements.append({"type": "text", "x": left, "y": y,
                     "size": small,
                     "text": ("No tax computed — this invoice's total"
                              " is the sum of its lines only.")})
    pages.append(elements)
    return pages


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _get_seller(store):
    """The stored seller details (a dict) or None."""
    if store._missing():
        return None
    with _LOCK:
        conn = store._connect()
        try:
            row = conn.execute(
                "SELECT name, address, email, phone FROM seller"
                " ORDER BY updated DESC LIMIT 1").fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    return {"name": row[0], "address": row[1], "email": row[2],
            "phone": row[3]}


def _render_pdf(store, a):
    """Render an erp invoice as a PDF. Returns (render_id, path,
    sha256, pages, invoice_id). Writes the PDF, verifies it with
    the stdlib xref parser, then records the render + the ledger
    event (A1-store: re-read before claiming). Raises _Refusal."""
    invoice_id = _clean_str(a["invoice_id"], "invoice_id", limit=100)
    template = str(a["template"] or "classic").strip().lower()
    if template not in _TEMPLATES:
        raise _Refusal("template must be one of: %s"
                       % ", ".join(_TEMPLATES))
    data = _read_erp_invoice(invoice_id)
    # Lines: from the order (frozen) for an order-based invoice;
    # operator-supplied for a standalone invoice (whose lines are
    # not stored by erp); none at all renders a summary-only PDF.
    if data["lines_from"] == "order":
        pass  # data["lines"] already holds the order's lines (dicts)
    else:
        parsed, supplied_total = _parse_render_lines(a["lines"])
        if parsed:
            if supplied_total != data["total_cents"]:
                raise _Refusal(
                    "the supplied lines total %s but the invoice"
                    " total is %s — the lines must match the invoice"
                    % (_fmt_money(supplied_total),
                       _fmt_money(data["total_cents"])))
            data["lines"] = [{"description": d, "quantity": q,
                              "unit_cents": u, "line_total": t}
                             for d, q, u, t in parsed]
        else:
            data["lines"] = []
    seller = _get_seller(store)
    # Per-call seller overrides.
    overrides = {}
    for key in ("seller_name", "seller_address", "seller_email",
                "seller_phone"):
        if a.get(key) is not None:
            overrides[key.replace("seller_", "")] = _clean_str(
                a[key], key)
    if overrides:
        seller = dict(seller or {})
        seller.update(overrides)
    pages = _layout_invoice(data, template, seller)
    output = a["output_path"]
    if output is None:
        output = str(Path(DATA_DIR) / "invoice_renders"
                     / ("%s.pdf" % invoice_id))
    else:
        output = _clean_str(output, "output_path", limit=_MAX_PATH)
        if not output.lower().endswith(".pdf"):
            raise _Refusal("output_path must end in .pdf")
    sha256, page_count = _write_pdf(output, pages)
    raw = Path(output).read_bytes()
    ok, verified_pages, err = _verify_pdf_bytes(raw)
    if not ok or verified_pages != page_count:
        raise _Refusal("the rendered PDF failed verification (%s) —"
                       " nothing was claimed" % (err or "page mismatch"))
    payload = {"action": "render", "invoice_id": invoice_id,
               "template": template, "output_path": output,
               "pdf_sha256": sha256, "pdf_pages": page_count}
    with _LOCK:
        conn = store._connect()
        try:
            rid = store.next_id(conn, "RND")
            conn.execute(
                "INSERT INTO renders (id, invoice_id, template,"
                " output_path, pdf_sha256, pdf_pages, created)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rid, invoice_id, template, output, sha256,
                 page_count, time.time()))
            store._append_event_locked(conn, "invoice.render", payload)
            conn.commit()
            row = conn.execute(
                "SELECT id, pdf_sha256 FROM renders WHERE id = ?",
                (rid,)).fetchone()
            ev = _last_event_payload(conn)
        finally:
            conn.close()
    if row is None or row[1] != sha256 or ev != payload:
        raise _Refusal("the render write did not re-read — nothing"
                       " was claimed")
    return rid, output, sha256, page_count, invoice_id


def _do_render(store, a):
    rid, output, sha256, pages, invoice_id = _render_pdf(store, a)
    return ("rendered invoice %s as %s (%d page%s, sha256 %s…)"
            % (invoice_id, output, pages, "" if pages == 1 else "s",
               sha256[:12]))


def _do_template(store, a):
    kind = str(a.get("kind") or "list").strip().lower()
    if kind == "list":
        out = ["templates:"]
        for name in _TEMPLATES:
            out.append("  %s — %s" % (name, _TEMPLATES_DESC[name]))
        return "\n".join(out)
    if kind == "get":
        name = str(a.get("template") or "").strip().lower()
        if name not in _TEMPLATES:
            raise _Refusal("template must be one of: %s"
                           % ", ".join(_TEMPLATES))
        return "%s — %s" % (name, _TEMPLATES_DESC[name])
    raise _Refusal("unknown kind %r for template — use list / get"
                   % kind)


def _do_seller(store, a):
    kind = str(a.get("kind") or "get").strip().lower()
    if kind == "get":
        seller = _get_seller(store)
        if seller is None:
            return ("No seller details stored yet — set them with"
                    " action 'seller', kind 'set' (name, address,"
                    " email, phone).")
        out = ["seller: %s" % seller["name"]]
        for field in ("address", "email", "phone"):
            if seller.get(field):
                out.append("  %s: %s" % (field, seller[field]))
        return "\n".join(out)
    if kind == "set":
        name = _clean_str(a.get("seller_name"), "seller_name")
        address = _clean_str(a.get("seller_address"), "seller_address",
                             required=False)
        email = _clean_str(a.get("seller_email"), "seller_email",
                           required=False)
        phone = _clean_str(a.get("seller_phone"), "seller_phone",
                           required=False)
        if email is not None and not _EMAIL_RE.match(email):
            raise _Refusal("seller_email must look like an email"
                           " address")
        payload = {"action": "seller", "kind": "set", "name": name,
                   "address": address, "email": email, "phone": phone}
        with _LOCK:
            conn = store._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO seller (id, name, address,"
                    " email, phone, updated) VALUES (?, ?, ?, ?, ?, ?)",
                    ("seller", name, address, email, phone,
                     time.time()))
                store._append_event_locked(conn, "invoice.seller",
                                           payload)
                conn.commit()
                row = conn.execute(
                    "SELECT name FROM seller WHERE id = 'seller'"
                    ).fetchone()
                ev = _last_event_payload(conn)
            finally:
                conn.close()
        if row is None or row[0] != name or ev != payload:
            raise _Refusal("the seller write did not re-read —"
                           " nothing was claimed")
        return "seller set: %s" % name
    raise _Refusal("unknown kind %r for seller — use set / get" % kind)


def _do_send(store, a):
    """Render the PDF, then send it as a Gmail attachment. This
    SENDS real mail — the action is confirm-gated (C-14)."""
    to = _clean_str(a["to"], "to", limit=200)
    if not _EMAIL_RE.match(to):
        raise _Refusal("to must look like an email address")
    rid, output, sha256, pages, invoice_id = _render_pdf(store, a)
    subject = a.get("subject")
    if subject is None:
        subject = "Invoice %s" % invoice_id
    else:
        subject = _clean_str(subject, "subject", limit=200)
    message = a.get("message")
    if message is None:
        message = ("Please find invoice %s attached."
                   % invoice_id)
    else:
        message = _clean_str(message, "message", limit=2000,
                             required=False) or message
    # The Gmail send (the send_email tool's handler, extended in
    # this batch with an optional `attachments` property). A
    # function-local import keeps the module importable without the
    # Gmail stack.
    from .google_services_gmail import send_email as _gmail_send
    result = _gmail_send(to=to, subject=subject, body=message,
                         attachments=[output])
    if not isinstance(result, str) or not result.startswith("Email sent"):
        raise _Refusal("the email was not sent (%s)" % result)
    gmail_id = ""
    marker = "Gmail message id "
    if marker in result:
        gmail_id = result.split(marker, 1)[1].rstrip(").").strip()
    payload = {"action": "send", "render_id": rid,
               "invoice_id": invoice_id, "to": to,
               "gmail_message_id": gmail_id}
    with _LOCK:
        conn = store._connect()
        try:
            sid = "SND-%s" % rid.split("-", 1)[1]
            conn.execute(
                "INSERT INTO sends (id, render_id, invoice_id,"
                " recipient, gmail_message_id, created)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (sid, rid, invoice_id, to, gmail_id, time.time()))
            store._append_event_locked(conn, "invoice.send", payload)
            conn.commit()
            row = conn.execute(
                "SELECT id FROM sends WHERE id = ?", (sid,)).fetchone()
            ev = _last_event_payload(conn)
        finally:
            conn.close()
    if row is None or ev != payload:
        raise _Refusal("the send record did not re-read — the email"
                       " was sent but the record was not claimed;"
                       " check the ledger")
    return ("sent invoice %s to %s with the PDF attached (%s)"
            % (invoice_id, to, output))


def _do_list(store, a):
    if store._missing():
        return "No invoices rendered yet."
    limit = _clean_limit(a["limit"])
    with _LOCK:
        conn = store._connect()
        try:
            rows = conn.execute(
                "SELECT id, invoice_id, template, output_path,"
                " pdf_pages FROM renders ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        finally:
            conn.close()
    if not rows:
        return "No invoices rendered yet."
    out = ["renders (%d):" % len(rows)]
    for rid, iid, template, path, pages in rows:
        out.append("  %s — invoice %s — %s — %d page%s — %s"
                   % (rid, iid, template, pages,
                      "" if pages == 1 else "s", path))
    return "\n".join(out)


def _do_get(store, a):
    rid = _clean_str(a["id"], "id", limit=100)
    row = store.get_row("renders", rid)
    if row is None:
        raise _Refusal("no render with id %s" % rid)
    (rid, iid, template, path, sha256, pages, created) = row
    out = ["%s — invoice %s — template %s — %d page%s"
           % (rid, iid, template, pages, "" if pages == 1 else "s"),
           "  path: %s" % path,
           "  sha256: %s" % sha256]
    return "\n".join(out)


def _do_status(store, a):
    if store._missing():
        return ("invoice: no renders yet, ledger empty.\n"
                "  No web app, no invoice record (erp keeps that),"
                " no tax computed — this tool renders and sends"
                " invoices as PDFs.")
    renders = store.count("renders")
    sends = store.count("sends")
    ok, events = store.verify_chain()
    seller = _get_seller(store)
    out = ["invoice: %d render(s), %d send(s), ledger %s (%d event(s))."
           % (renders, sends, "intact" if ok else "BROKEN", events),
           "  templates: %s." % ", ".join(_TEMPLATES),
           "  seller: %s." % (seller["name"] if seller
                              else "not set"),
           "  No web app, no invoice record (erp keeps that), no tax"
           " computed — this tool renders and sends invoices as PDFs."]
    return "\n".join(out)


def _do_events(store, a):
    kind = str(a.get("kind") or "list").strip().lower()
    if kind == "verify":
        ok, count = store.verify_chain()
        if ok:
            return "ledger intact (%d event(s))" % count
        return "ledger BROKEN — the chain does not verify (%d event(s))" % count
    if kind == "list":
        limit = _clean_limit(a["limit"])
        rows = store.list_events(limit)
        if not rows:
            return "No events yet — the ledger is empty."
        out = ["events (%d):" % len(rows)]
        for eid, ts, k, payload, prev_hash, digest in rows:
            out.append("  #%d %s — %s" % (eid, k, payload[:120]))
        return "\n".join(out)
    raise _Refusal("unknown kind %r for events — use list / verify"
                   % kind)


# ---------------------------------------------------------------------------
# Handler + registration
# ---------------------------------------------------------------------------

def invoice(action=None, kind=None, invoice_id=None, template=None,
            output_path=None, lines=None, seller_name=None,
            seller_address=None, seller_email=None, seller_phone=None,
            to=None, subject=None, message=None, id=None, limit=None,
            _store=None):
    """Render an erp invoice as a PDF (three templates) and, on
    request, send it by email with the PDF attached. The invoice
    record itself lives in erp — this tool renders and sends it.
    Sending real mail is confirm-gated; rendering is local. Returns
    a plain-text answer."""
    store = _store if _store is not None else _InvoiceStore()
    act = str(action or "").strip().lower()
    if act not in _ACTIONS:
        return ("refused: unknown action %r — use render / template /"
                " seller / send / list / get / status / events" % act)
    a = {"action": act, "kind": kind, "invoice_id": invoice_id,
         "template": template, "output_path": output_path,
         "lines": lines, "seller_name": seller_name,
         "seller_address": seller_address, "seller_email": seller_email,
         "seller_phone": seller_phone, "to": to, "subject": subject,
         "message": message, "id": id, "limit": limit}
    try:
        if act == "render":
            return _do_render(store, a)
        if act == "template":
            return _do_template(store, a)
        if act == "seller":
            return _do_seller(store, a)
        if act == "send":
            return _do_send(store, a)
        if act == "list":
            return _do_list(store, a)
        if act == "get":
            return _do_get(store, a)
        if act == "status":
            return _do_status(store, a)
        return _do_events(store, a)
    except _Refusal as exc:
        return "refused: %s" % exc


def register_all(registry) -> None:
    """Register the invoice pack (R19-5, al1abb/invoify
    inspiration): invoice — render an erp invoice as a PDF (three
    templates: classic, modern, minimal) with a minimal stdlib PDF
    writer, and send it by email with the PDF attached (the
    send_email tool, extended in this batch with an optional
    `attachments` property). The invoice record itself lives in erp;
    this tool renders and sends it. `send` is confirm-gated (it SENDS
    real mail); rendering, templates, the seller record and the reads
    stay free. Own stdlib code over the invoice shapes; nothing
    adopted — no Next.js, no TypeScript, no Shadcn, no web app, no
    PDF library. The module owns its catalogue via register_all
    (same pattern as the other packs)."""
    registry.register(
        name="invoice",
        description=(
            "Render an erp invoice as a PDF and send it by email. "
            "Pick a template (classic, modern, minimal), render the "
            "invoice to a PDF file (written by a minimal stdlib PDF "
            "writer — no PDF library), and optionally send it as a "
            "Gmail attachment. The invoice record itself lives in "
            "erp — this tool renders and sends it; it never computes "
            "tax and never moves money. Sending real mail needs the "
            "user's approval first. Use when the user wants a PDF of "
            "an invoice, a different invoice layout, or to email an "
            "invoice to a customer."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "One of: 'render' (render an erp invoice as a PDF), 'template' (list / get the built-in templates), 'seller' (set / get the business details), 'send' (render and email the PDF as an attachment), 'list', 'get', 'status' or 'events'"},
                "kind": {"type": "string",
                         "description": "The sub-action: 'list' or 'get' for template; 'set' or 'get' for seller; 'list' or 'verify' for events (list if left out)"},
                "invoice_id": {"type": "string",
                               "description": "With render / send, the erp invoice id (INV-0001) to render or send"},
                "template": {"type": "string",
                             "description": "With render / send / template get, one of 'classic', 'modern' or 'minimal' (classic if left out)"},
                "output_path": {"type": "string",
                                "description": "With render / send, where to write the PDF (must end in .pdf; if left out, the default renders folder under the app data dir)"},
                "lines": {"type": "array",
                          "description": "With render / send, for a standalone invoice (whose lines are not stored by erp), the lines as [{description, quantity, unit_price_cents}] or [{product_id, quantity, price_cents}] — they must total the invoice total; not needed for an order-based invoice (the lines come from the sales order)"},
                "seller_name": {"type": "string",
                                "description": "With seller set, or as a per-render override, the business name (at most 120 chars)"},
                "seller_address": {"type": "string",
                                   "description": "With seller set, or as a per-render override, the business address"},
                "seller_email": {"type": "string",
                                 "description": "With seller set, or as a per-render override, the business email address"},
                "seller_phone": {"type": "string",
                                 "description": "With seller set, or as a per-render override, the business phone number"},
                "to": {"type": "string",
                       "description": "With send, the recipient email address"},
                "subject": {"type": "string",
                            "description": "With send, the email subject (defaults to 'Invoice <id>')"},
                "message": {"type": "string",
                            "description": "With send, the email body (defaults to a short note naming the invoice)"},
                "id": {"type": "string",
                       "description": "With get, the render id (RND-0001)"},
                "limit": {"type": "integer",
                          "description": "With list / events, most rows shown (20 if left out, max 100)"},
            },
            "required": ["action"],
        },
        handler=invoice,
    )
