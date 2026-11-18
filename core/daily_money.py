from __future__ import annotations  # INTENTIONAL (skills split B21): moved code was written under postponed evaluation.
import datetime
import re
from xml.sax.saxutils import escape as _xml_escape
# INTENTIONAL (skills split B21): shared store plumbing lives in daily_helpers; record_money_event resolves these by bare name.
from .daily_helpers import _load_json, _save_json_atomic

# ===========================================================================
# Money paperwork engine — organisation + flagging ONLY
# ===========================================================================

SUPPORTED_DOC_KINDS = frozenset({"AIS", "26AS", "16", "P&L", "PL"})

# Indian PNR: 10 digits. Redaction targets the PNR in context
# ("PNR 1234567890", "PNR:1234567890") AND any bare 10-digit run
# that a record function is asked to store — a record must never
# be the place a PNR survives, so the record path is the wider
# net and the plain-text helper covers the labelled form.
_PNR_LABELLED_RE = re.compile(r"(PNR\s*[:#]?\s*)(\d{10})\b", re.IGNORECASE)
_PNR_BARE_RE = re.compile(r"\b\d{10}\b")


def redact_pnr(text) -> str:
    """Redact PNRs from text. Labelled PNRs keep the label
    ("PNR [REDACTED]"); the helper never returns the digits."""
    if text is None:
        return ""
    out = _PNR_LABELLED_RE.sub(r"\1[REDACTED]", str(text))
    return out


def redact_pnr_for_record(text) -> str:
    """Record-path redaction: labelled PNRs AND any bare
    10-digit run are redacted before storage/return. A PNR is a
    secret — it is never logged and never egressed, so no record
    function may store one in any form."""
    if text is None:
        return ""
    out = redact_pnr(text)
    out = _PNR_BARE_RE.sub("[REDACTED]", out)
    return out


def record_money_event(store_path, event: dict) -> dict:
    """Append one money-paperwork record to the caller's store
    (JSON list under "money_records"), with PNRs redacted in
    every string field BEFORE writing. Returns the stored
    (redacted) record. The store path is caller-provided."""
    if not store_path:
        raise ValueError("record_money_event requires a caller-provided store_path")
    stored = {}
    for key, value in (event or {}).items():
        stored[str(key)] = redact_pnr_for_record(value) if isinstance(value, str) else value
    stored["recorded_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    data = _load_json(store_path)
    records = data.get("money_records") or []
    if not isinstance(records, list):
        records = []
    records.append(stored)
    data["money_records"] = records
    _save_json_atomic(store_path, data)
    return stored


def organise_document(kind: str, rows) -> dict:
    """Organise one paperwork document locally: classify rows,
    total amounts, and flag discrepancies. Organisation ONLY —
    no tax computation, no advice, no filing. ``rows`` is a list
    of dicts with at least "amount" (number or numeric string)
    and optional "label"/"section"/"stated_total".

    Flags raised (never silently smoothed over):
      * "total_mismatch" — a row's stated_total disagrees with
        the sum of its section's amounts;
      * "duplicate_row" — identical (label, amount) twice;
      * "missing_amount" — a row with no parseable amount;
      * "unknown_kind" — the doc kind is not AIS/26AS/16/P&L.
    """
    kind = str(kind or "").strip().upper()
    if kind == "PL":
        kind = "P&L"
    result = {"kind": kind, "row_count": 0, "total": 0.0,
              "sections": {}, "flags": []}
    if kind not in SUPPORTED_DOC_KINDS:
        result["flags"].append({
            "flag": "unknown_kind",
            "detail": f"unsupported document kind {kind!r}; "
                      "supported: AIS, 26AS, 16, P&L."})
        return result
    seen = set()
    section_sums: dict = {}
    section_stated: dict = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        result["row_count"] += 1
        label = str(row.get("label", "")).strip()
        section = str(row.get("section", "default")).strip() or "default"
        raw_amount = row.get("amount")
        try:
            amount = float(str(raw_amount).replace(",", "").strip())
        except (ValueError, TypeError, AttributeError):
            result["flags"].append({
                "flag": "missing_amount",
                "detail": f"row {label!r} has no parseable amount."})
            continue
        key = (label.lower(), amount)
        if key in seen:
            result["flags"].append({
                "flag": "duplicate_row",
                "detail": f"duplicate row {label!r} amount {amount}."})
        seen.add(key)
        section_sums[section] = section_sums.get(section, 0.0) + amount
        result["total"] += amount
        if row.get("stated_total") is not None:
            try:
                section_stated[section] = float(
                    str(row["stated_total"]).replace(",", "").strip())
            except (ValueError, TypeError, AttributeError):
                pass
    result["sections"] = {s: round(v, 2) for s, v in section_sums.items()}
    result["total"] = round(result["total"], 2)
    for section, stated in section_stated.items():
        summed = round(section_sums.get(section, 0.0), 2)
        if abs(summed - round(stated, 2)) > 0.01:
            result["flags"].append({
                "flag": "total_mismatch",
                "detail": f"section {section!r} sums to {summed} but "
                          f"states {round(stated, 2)} — flagged, not adjusted."})
    return result


def flag_discrepancies(documents: list) -> list[dict]:
    """Cross-document discrepancy FLAGGING only: compare the
    same section's totals across organised documents. Returns
    flag dicts; it never computes tax, never advises, never
    files. ``documents`` are organise_document() results."""
    flags = []
    by_section: dict = {}
    for doc in documents or []:
        if not isinstance(doc, dict):
            continue
        for section, total in (doc.get("sections") or {}).items():
            by_section.setdefault(section, []).append((doc.get("kind"), total))
    for section, entries in by_section.items():
        totals = {round(float(t), 2) for _, t in entries}
        if len(totals) > 1:
            flags.append({
                "flag": "cross_document_mismatch",
                "detail": f"section {section!r} differs across documents: "
                          + ", ".join(f"{k}={t}" for k, t in entries)
                          + " — flagged for the user to reconcile."})
    return flags


def refuse_portal_login(portal: str) -> dict:
    """Portal-login automation (incometax / GST) is REFUSED by
    design (INV-20). Always a refusal, for any portal name."""
    name = str(portal or "").strip() or "tax portal"
    return {
        "refused": True,
        "reason": (f"portal login to {name} is refused by design "
                   "(INV-20): this assistant never automates sign-in "
                   "to incometax or GST portals. Sign in yourself in "
                   "your own browser; the assistant can organise "
                   "documents you provide, nothing more."),
    }


RAIL_DOCTRINE = (
    "three-tier rail-booking doctrine: "
    "(1) no self-serve rail-booking automation is built into this "
    "assistant — there is no lawful self-serve rail-booking path "
    "for it to use in India; "
    "(2) rail bookings remain a human action through authorised "
    "channels (the user booking directly, or an authorised agent); "
    "(3) this assistant only organises trip details the user "
    "provides and refuses to submit any booking.")


def refuse_rail_booking(details: str = "") -> dict:
    """NO self-serve rail-booking is built. Always returns a
    refusal record stating the three-tier doctrine. The supplied
    details are never acted on and any PNR in them is redacted
    from the record."""
    return {
        "refused": True,
        "reason": RAIL_DOCTRINE,
        "details": redact_pnr_for_record(details),
    }


def build_facturx_xml(invoice: dict) -> str:
    """Build a minimal EN 16931 (BASIC profile) CrossIndustryInvoice
    XML string from an invoice dict (seller, buyer, id, date,
    currency, lines of {description, quantity, unit_price}, total).
    Own-code XML shaping — no Factur-X library is used, so no
    Factur-X library licence is involved. This is the invoice
    SHAPE; embedding it into a PDF/A-3 is ``embed_facturx``'s
    job, and the facade there is explicit about its limits."""
    inv = invoice or {}
    lines = inv.get("lines") or []
    currency = str(inv.get("currency", "EUR"))
    # Attribute-safe form, defined BEFORE the line loop uses it.
    currency_attr = _xml_escape(currency, {'"': "&quot;"})
    total = 0.0
    line_xml = []
    for index, line in enumerate(lines, start=1):
        if not isinstance(line, dict):
            continue
        qty = float(line.get("quantity", 1) or 1)
        price = float(line.get("unit_price", 0) or 0)
        line_total = round(qty * price, 2)
        total += line_total
        # MN-7.12 fix (v5.66 leaf 4): escape ALL free text, not just
        # "&"/"<" in descriptions — a seller/buyer name or a
        # description containing "&", "<", ">" or quotes used to
        # break the XML. _xml_escape covers &, <, >; quotes are
        # escaped for attribute values below.
        desc = _xml_escape(str(line.get("description", "")))
        line_xml.append(
            f"<ram:IncludedSupplyChainTradeLineItem>"
            f"<ram:AssociatedDocumentLineDocument>"
            f"<ram:LineID>{index}</ram:LineID>"
            f"</ram:AssociatedDocumentLineDocument>"
            f"<ram:SpecifiedTradeProduct><ram:Name>{desc}</ram:Name>"
            f"</ram:SpecifiedTradeProduct>"
            f"<ram:SpecifiedLineTradeAgreement>"
            f"<ram:NetPriceProductTradePrice>"
            f"<ram:ChargeAmount currencyID=\"{currency_attr}\">{price:.2f}</ram:ChargeAmount>"
            f"</ram:NetPriceProductTradePrice></ram:SpecifiedLineTradeAgreement>"
            f"<ram:SpecifiedLineTradeDelivery>"
            f"<ram:BilledQuantity unitCode=\"C62\">{qty}</ram:BilledQuantity>"
            f"</ram:SpecifiedLineTradeDelivery>"
            f"<ram:SpecifiedLineTradeSettlement>"
            f"<ram:SpecifiedTradeSettlementLineMonetarySummation>"
            f"<ram:LineTotalAmount currencyID=\"{currency_attr}\">{line_total:.2f}</ram:LineTotalAmount>"
            f"</ram:SpecifiedTradeSettlementLineMonetarySummation>"
            f"</ram:SpecifiedLineTradeSettlement>"
            f"</ram:IncludedSupplyChainTradeLineItem>")
    stated = inv.get("total")
    try:
        stated_total = float(stated) if stated is not None else total
    except (ValueError, TypeError):
        stated_total = total
    # MN-7.12 fix (v5.66 leaf 4): the original XML used the udt:
    # prefix with NO xmlns:udt declaration, so the output was not
    # namespace-well-formed. The declaration below is pinned by
    # test_v566_leaf4_tax.py (test_build_facturx_xml_shape).
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rsm:CrossIndustryInvoice '
        'xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100" '
        'xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100" '
        'xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">'
        '<rsm:ExchangedDocument>'
        f"<ram:ID>{_xml_escape(str(inv.get('id', '')))}</ram:ID>"
        f"<ram:IssueDateTime><udt:DateTimeString format=\"102\">"
        f"{str(inv.get('date', '')).replace('-', '')}</udt:DateTimeString></ram:IssueDateTime>"
        '</rsm:ExchangedDocument>'
        '<rsm:SupplyChainTradeTransaction>'
        '<ram:ApplicableHeaderTradeAgreement>'
        f"<ram:SellerTradeParty><ram:Name>{_xml_escape(str(inv.get('seller', '')))}</ram:Name></ram:SellerTradeParty>"
        f"<ram:BuyerTradeParty><ram:Name>{_xml_escape(str(inv.get('buyer', '')))}</ram:Name></ram:BuyerTradeParty>"
        '</ram:ApplicableHeaderTradeAgreement>'
        + "".join(line_xml) +
        '<ram:ApplicableHeaderTradeSettlement>'
        '<ram:SpecifiedTradeSettlementHeaderMonetarySummation>'
        f"<ram:GrandTotalAmount currencyID=\"{currency_attr}\">{stated_total:.2f}</ram:GrandTotalAmount>"
        '</ram:SpecifiedTradeSettlementHeaderMonetarySummation>'
        '</ram:ApplicableHeaderTradeSettlement>'
        '</rsm:SupplyChainTradeTransaction>'
        '</rsm:CrossIndustryInvoice>')
    return xml


def embed_facturx(pdf_path, xml_text: str, out_path) -> dict:
    """Attach EN 16931 XML to a PDF via the pypdf library API.

    FACADE when pypdf is absent: returns {"ok": False,
    "facade": True, ...} and writes NOTHING — it never claims a
    Factur-X file was produced. When pypdf IS present, the XML is
    embedded as an attachment (factur-x.xml) and — MN-7.12
    completion, v5.66 leaf 4, tested against real pypdf 6.21.2 —
    marked as an associated file: the catalog gains an /AF entry
    pointing at the embedded file spec with /AFRelationship
    /Source, the Factur-X associated-file shape. HONEST LIMIT:
    an attachment + /AF marking alone is not a certified PDF/A-3
    file (PDF/A-3 also needs XMP metadata and conformance
    marking); the result says "embedded", never "PDF/A-3
    certified". pypdf is lazy-imported so its absence breaks
    nothing else. Invoice generation only: no tax computation,
    no filing.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception:  # noqa: BLE001 — pypdf absent: facade, not failure
        return {"ok": False, "facade": True,
                "detail": ("pypdf is not available, so no Factur-X PDF "
                           "was produced (facade only). Install pypdf "
                           "to enable XML embedding; nothing was written.")}
    try:
        reader = PdfReader(str(pdf_path))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_attachment("factur-x.xml", xml_text.encode("utf-8"))
        # Associated-file marking (best-effort: a failure here
        # degrades to attachment-only, never to a false claim).
        af_marked = False
        try:
            from pypdf.generic import ArrayObject, NameObject
            root = writer._root_object
            names = root["/Names"]["/EmbeddedFiles"]["/Names"]
            spec_ref = names[1]
            spec = spec_ref.get_object()
            spec[NameObject("/AFRelationship")] = NameObject("/Source")
            root[NameObject("/AF")] = ArrayObject([spec_ref])
            af_marked = True
        except Exception:  # noqa: BLE001 — attachment still stands
            af_marked = False
        with open(out_path, "wb") as handle:
            writer.write(handle)
    except Exception as exc:  # noqa: BLE001 — honest failure, no file claimed
        return {"ok": False, "facade": False, "detail": f"embedding failed: {exc}"}
    return {"ok": True, "facade": False,
            "attachment": "factur-x.xml",
            "af_relationship": "Source" if af_marked else None,
            "detail": ("EN 16931 XML embedded as factur-x.xml. This is "
                       "an attachment, not a certified PDF/A-3 file.")}


