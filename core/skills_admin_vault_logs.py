import datetime
import re
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_admin_helpers import (
    _MAX_NAME_CHARS,
    _MAX_TEXT_CHARS,
    _add_months,
    _bad_date_line,
    _broken_note,
    _clean,
    _coerce_int,
    _days_words,
    _load,
    _nonempty,
    _parse_date,
    _save,
)


# ---------------------------------------------------------------------------
# DOCUMENT VAULT — an index of important documents: name, issuer,
# expiry. Never the document, never its number.
# ---------------------------------------------------------------------------

_DOCUMENTS_CATEGORY = "document"
_ID_NUMBER_RE = re.compile(r"\d{12,}")


def _load_documents() -> tuple[list, int]:
    """(entries, broken_count): {name, issuer, expiry, note}
    dicts. A blank expiry is a valid dateless record; a present
    but unparseable one makes the record broken."""
    def expiry_ok(value) -> bool:
        text = str(value or "").strip()
        return not text or _parse_date(text) is not None

    entries, broken = _load(_DOCUMENTS_CATEGORY, {
        "name": _nonempty, "expiry": expiry_ok})
    for entry in entries:
        entry["name"] = str(entry.get("name") or "")
        entry["issuer"] = str(entry.get("issuer") or "")
        entry["expiry"] = str(entry.get("expiry") or "").strip()
        entry["note"] = str(entry.get("note") or "")
    return entries, broken


def document_vault(action: str, name: str = "", issuer: str = "",
                   expiry: str = "", note: str = "") -> str:
    """Index a document by name, issuer and expiry date, or list
    the vault, soonest expiry first. Refuses any field that
    looks like a full ID number — names and dates only."""
    action = str(action or "").strip().lower()
    if action not in ("save", "list"):
        return ("Document vault actions are 'save' and 'list' — "
                "e.g. document_vault with action 'save', a "
                "document name and its expiry date.")
    if action == "list":
        try:
            entries, broken = _load_documents()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the document vault: {e}"
        if not entries:
            base = ("The document vault is empty. Add a "
                    "document with document_vault, action "
                    "'save', its name and expiry date.")
            if broken:
                base += " " + _broken_note(broken, "document")
            return base
        today = datetime.date.today()
        dated = [e for e in entries if e["expiry"]]
        dateless = [e for e in entries if not e["expiry"]]
        dated.sort(key=lambda e: e["expiry"])
        dateless.sort(key=lambda e: e["name"].lower())
        lines = [f"Document vault ({len(entries)}):"]
        for entry in dated:
            day = _parse_date(entry["expiry"])
            line = (f"• {entry['name']} — expires "
                    f"{entry['expiry']} "
                    f"({_days_words((day - today).days)})")
            if entry["issuer"]:
                line += f" — issued by {entry['issuer']}"
            if entry["note"]:
                line += f" — {entry['note']}"
            lines.append(line)
        for entry in dateless:
            line = f"• {entry['name']} — no expiry date recorded"
            if entry["issuer"]:
                line += f" — issued by {entry['issuer']}"
            if entry["note"]:
                line += f" — {entry['note']}"
            lines.append(line)
        if broken:
            lines.append(_broken_note(broken, "document"))
        return "\n".join(lines)
    # action == "save"
    clean_name = _clean(name)
    if not clean_name:
        return "Saving to the vault needs the document's name."
    if len(clean_name) > _MAX_NAME_CHARS:
        return (f"That name is {len(clean_name)} characters — "
                f"document names are capped at "
                f"{_MAX_NAME_CHARS}.")
    clean_issuer = _clean(issuer)
    if len(clean_issuer) > _MAX_NAME_CHARS:
        return (f"That issuer is {len(clean_issuer)} characters "
                f"— issuers are capped at {_MAX_NAME_CHARS}.")
    clean_note = _clean(note)
    if len(clean_note) > _MAX_TEXT_CHARS:
        return (f"That note is {len(clean_note)} characters — "
                f"notes are capped at {_MAX_TEXT_CHARS}.")
    expiry_text = str(expiry or "").strip()
    # The privacy rule, checked on every field before anything
    # is stored: a run of 12+ digits reads as a full ID number,
    # and the vault never keeps those.
    for label, field in (("name", clean_name),
                         ("issuer", clean_issuer),
                         ("expiry date", expiry_text),
                         ("note", clean_note)):
        if _ID_NUMBER_RE.search(field):
            return (f"That {label} contains what looks like a "
                    "full ID number (12 or more digits in a "
                    "row). The document vault keeps names and "
                    "expiry dates only — please don't store full "
                    "ID numbers here. Nothing was saved.")
    expiry_day = None
    if expiry_text:
        expiry_day = _parse_date(expiry_text)
        if expiry_day is None:
            return _bad_date_line(expiry_text)
    payload = {"name": clean_name, "issuer": clean_issuer,
               "expiry": expiry_day.isoformat() if expiry_day
               else "", "note": clean_note}
    try:
        existed = _save(_DOCUMENTS_CATEGORY, clean_name.lower(),
                        payload)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't save the document: {e}"
    result = (f"{'Updated' if existed else 'Saved'} "
              f"'{clean_name}' in the document vault")
    if clean_issuer:
        result += f" (issued by {clean_issuer})"
    if expiry_day:
        result += f" — expiry {expiry_day.isoformat()}."
    else:
        result += " — no expiry date recorded."
    result += " Names and dates only — no document numbers are kept."
    return result


# ---------------------------------------------------------------------------
# WARRANTY LOG — purchases with a computed cover end date
# ---------------------------------------------------------------------------

_WARRANTY_CATEGORY = "warranty"


def _load_warranties() -> tuple[list, int]:
    """(entries, broken_count): {product, bought_on,
    warranty_months, warranty_end, shop} dicts."""
    def date_ok(value) -> bool:
        return _parse_date(value) is not None

    entries, broken = _load(_WARRANTY_CATEGORY, {
        "product": _nonempty, "warranty_end": date_ok})
    for entry in entries:
        entry["product"] = str(entry.get("product") or "")
        entry["bought_on"] = str(entry.get("bought_on") or "")
        entry["warranty_end"] = str(
            entry.get("warranty_end") or "").strip()
        entry["shop"] = str(entry.get("shop") or "")
        try:
            entry["warranty_months"] = int(
                float(entry.get("warranty_months")))
        except (TypeError, ValueError):
            entry["warranty_months"] = 0
    return entries, broken


def warranty_log(action: str, product: str = "",
                 bought_on: str = "", warranty_months=0,
                 shop: str = "") -> str:
    """Record a purchase's warranty (computing its end date
    from the bought date plus the warranty length) or list the
    log, soonest end first."""
    action = str(action or "").strip().lower()
    if action not in ("save", "list"):
        return ("Warranty log actions are 'save' and 'list' — "
                "e.g. warranty_log with action 'save', the "
                "product, the date it was bought and the "
                "warranty length in months.")
    if action == "list":
        try:
            entries, broken = _load_warranties()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the warranty log: {e}"
        if not entries:
            base = ("No warranties logged yet. Add one with "
                    "warranty_log, action 'save', the product, "
                    "the date it was bought and the warranty "
                    "length in months.")
            if broken:
                base += " " + _broken_note(broken, "warranty")
            return base
        today = datetime.date.today()
        entries.sort(key=lambda e: e["warranty_end"])
        lines = [f"Warranty log ({len(entries)}):"]
        for entry in entries:
            end = _parse_date(entry["warranty_end"])
            delta = (end - today).days
            if delta < 0:
                status = (f"out of warranty "
                          f"({_days_words(delta)})")
            elif delta == 0:
                status = "cover ends today"
            else:
                status = (f"covered, ends "
                          f"{_days_words(delta)}")
            line = (f"• {entry['product']} — cover until "
                    f"{entry['warranty_end']} ({status})")
            details = []
            if entry["bought_on"]:
                details.append(f"bought {entry['bought_on']}")
            if entry["warranty_months"]:
                details.append(
                    f"{entry['warranty_months']} months")
            if entry["shop"]:
                details.append(f"from {entry['shop']}")
            if details:
                line += " — " + ", ".join(details)
            lines.append(line)
        if broken:
            lines.append(_broken_note(broken, "warranty"))
        return "\n".join(lines)
    # action == "save"
    clean_product = _clean(product)
    if not clean_product:
        return "Saving a warranty needs the product's name."
    if len(clean_product) > _MAX_NAME_CHARS:
        return (f"That product name is {len(clean_product)} "
                f"characters — names are capped at "
                f"{_MAX_NAME_CHARS}.")
    bought_text = str(bought_on or "").strip()
    bought_day = _parse_date(bought_text)
    if bought_day is None:
        return _bad_date_line(bought_text)
    months = _coerce_int(warranty_months, 0, 0, 1200)
    if months < 1:
        return ("The warranty length needs to be a whole "
                "number of months, at least 1 — e.g. 12 or 24. "
                "Nothing was saved.")
    clean_shop = _clean(shop)
    if len(clean_shop) > _MAX_NAME_CHARS:
        return (f"That shop name is {len(clean_shop)} "
                f"characters — names are capped at "
                f"{_MAX_NAME_CHARS}.")
    end_day = _add_months(bought_day, months)
    payload = {"product": clean_product,
               "bought_on": bought_day.isoformat(),
               "warranty_months": months,
               "warranty_end": end_day.isoformat(),
               "shop": clean_shop}
    try:
        existed = _save(_WARRANTY_CATEGORY,
                        clean_product.lower(), payload)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't save the warranty: {e}"
    result = (f"{'Updated' if existed else 'Saved'} the "
              f"warranty for '{clean_product}' — bought "
              f"{bought_day.isoformat()}")
    if clean_shop:
        result += f" from {clean_shop}"
    result += (f", covered for {months} months until "
               f"{end_day.isoformat()}.")
    return result


# ---------------------------------------------------------------------------
# EXPIRY RADAR — one read across the vault and the warranty log
# ---------------------------------------------------------------------------


def expiry_radar(days=90) -> str:
    """What's expired and what's expiring within `days` days,
    across every stored document and warranty, soonest first."""
    window = _coerce_int(days, 90, 0, 3650)
    today = datetime.date.today()
    items = []  # (date, kind label, name)
    unreadable = 0
    try:
        documents, doc_broken = _load_documents()
        warranties, war_broken = _load_warranties()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the stored dates: {e}"
    unreadable += doc_broken + war_broken
    for entry in documents:
        if entry["expiry"]:
            items.append((_parse_date(entry["expiry"]),
                          "Document", entry["name"]))
    for entry in warranties:
        items.append((_parse_date(entry["warranty_end"]),
                      "Warranty", entry["product"]))
    broken_line = ""
    if unreadable:
        broken_line = (" " + _broken_note(unreadable,
                                          "stored record"))
    if not items:
        return ("Nothing on the expiry radar — no documents or "
                "warranties with dates are stored yet. Add them "
                "with document_vault and warranty_log."
                + broken_line)
    expired = sorted([i for i in items if i[0] < today],
                     key=lambda i: i[0])
    horizon = today + datetime.timedelta(days=window)
    soon = sorted([i for i in items if today <= i[0] <= horizon],
                  key=lambda i: i[0])
    later = [i for i in items if i[0] > horizon]
    if not expired and not soon:
        return (f"Nothing expired, and nothing expiring in "
                f"the next {window} day"
                f"{'s' if window != 1 else ''} — {len(later)} "
                f"dated item{'s' if len(later) != 1 else ''} "
                f"on file, all further out than that."
                + broken_line)
    lines = [f"Expiry radar (next {window} "
             f"day{'s' if window != 1 else ''}):"]
    if expired:
        lines.append("Expired:")
        for day, kind, name in expired:
            lines.append(f"• {kind} '{name}' — expired "
                         f"{day.isoformat()} "
                         f"({_days_words((day - today).days)})")
    if soon:
        lines.append("Coming up:")
        for day, kind, name in soon:
            delta = (day - today).days
            when = ("expires today" if delta == 0 else
                    f"expires {_days_words(delta)}")
            lines.append(f"• {kind} '{name}' — {when} "
                         f"({day.isoformat()})")
    if later:
        lines.append(f"({len(later)} more dated item"
                     f"{'s' if len(later) != 1 else ''} further "
                     "out than that window.)")
    if unreadable:
        lines.append(_broken_note(unreadable, "stored record"))
    return "\n".join(lines)
