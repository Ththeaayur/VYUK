import datetime
import os
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_admin_helpers import (
    _MAX_NAME_CHARS,
    _MAX_TEXT_CHARS,
    _bad_date_line,
    _broken_note,
    _clean,
    _coerce_bool,
    _coerce_float,
    _fmt_amount,
    _load,
    _nonempty,
    _parse_date,
    _save,
)


# ---------------------------------------------------------------------------
# GIFT LOG — ideas per person, kept apart from what's been given
# ---------------------------------------------------------------------------

_GIFT_CATEGORY = "gift"


def _load_gifts() -> tuple[list, int]:
    """(entries, broken_count): {person, idea, occasion,
    given} dicts."""
    entries, broken = _load(_GIFT_CATEGORY, {
        "person": _nonempty, "idea": _nonempty})
    for entry in entries:
        entry["person"] = str(entry.get("person") or "")
        entry["idea"] = str(entry.get("idea") or "")
        entry["occasion"] = str(entry.get("occasion") or "")
        entry["given"] = _coerce_bool(entry.get("given"))
    return entries, broken


def gift_log(action: str, person: str = "", idea: str = "",
             occasion: str = "", given=False) -> str:
    """Save a gift idea for a person (or record one as
    given), or list the log grouped by person with ideas and
    already-given kept separate."""
    action = str(action or "").strip().lower()
    if action not in ("save", "list"):
        return ("Gift log actions are 'save' and 'list' — "
                "e.g. gift_log with action 'save', a person "
                "and an idea; set given to true once it's "
                "been handed over.")
    if action == "list":
        try:
            entries, broken = _load_gifts()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the gift log: {e}"
        if not entries:
            base = ("The gift log is empty. Save an idea "
                    "with gift_log, action 'save', a person "
                    "and the idea.")
            if broken:
                base += " " + _broken_note(broken, "gift entry")
            return base
        by_person: dict[str, list] = {}
        for entry in entries:
            by_person.setdefault(
                entry["person"].lower(), []).append(entry)
        lines = [f"Gift log ({len(entries)}):"]
        for key in sorted(by_person):
            group = by_person[key]
            lines.append(f"{group[0]['person']}:")
            for entry in sorted(
                    group,
                    key=lambda e: (e["given"],
                                   e["idea"].lower())):
                tag = "Given" if entry["given"] else "Idea"
                line = f"• {tag}: {entry['idea']}"
                if entry["occasion"]:
                    line += f" — for {entry['occasion']}"
                lines.append(line)
        if broken:
            lines.append(_broken_note(broken, "gift entry"))
        return "\n".join(lines)
    # action == "save"
    clean_person = _clean(person)
    if not clean_person:
        return "Saving a gift idea needs the person's name."
    if len(clean_person) > _MAX_NAME_CHARS:
        return (f"That name is {len(clean_person)} characters "
                f"— names are capped at {_MAX_NAME_CHARS}.")
    clean_idea = _clean(idea)
    if not clean_idea:
        return "Saving a gift entry needs the idea itself."
    if len(clean_idea) > _MAX_TEXT_CHARS:
        return (f"That idea is {len(clean_idea)} characters — "
                f"entries are capped at {_MAX_TEXT_CHARS}.")
    clean_occasion = _clean(occasion)
    if len(clean_occasion) > _MAX_NAME_CHARS:
        return (f"That occasion is {len(clean_occasion)} "
                f"characters — it's capped at "
                f"{_MAX_NAME_CHARS}.")
    is_given = _coerce_bool(given)
    key = clean_person.lower() + " — " + clean_idea.lower()
    payload = {"person": clean_person, "idea": clean_idea,
               "occasion": clean_occasion, "given": is_given}
    try:
        existed = _save(_GIFT_CATEGORY, key, payload)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't save the gift entry: {e}"
    if is_given:
        return (f"{'Updated' if existed else 'Noted'}: "
                f"'{clean_idea}' marked as given to "
                f"{clean_person}.")
    result = (f"{'Updated' if existed else 'Saved'} the gift "
              f"idea '{clean_idea}' for {clean_person}")
    if clean_occasion:
        result += f" (for {clean_occasion})"
    return result + "."


# ---------------------------------------------------------------------------
# BORROW LOG — what's out, who has it, and for how long
# ---------------------------------------------------------------------------

_BORROW_CATEGORY = "borrow"


def _load_borrows() -> tuple[list, int]:
    """(entries, broken_count): {item, person, direction,
    date, status, returned_date} dicts, each carrying '_key'."""
    def date_ok(value) -> bool:
        return _parse_date(value) is not None

    entries, broken = _load(_BORROW_CATEGORY, {
        "item": _nonempty, "person": _nonempty,
        "date": date_ok})
    for entry in entries:
        entry["item"] = str(entry.get("item") or "")
        entry["person"] = str(entry.get("person") or "")
        entry["direction"] = (
            "borrowed" if str(entry.get("direction")) ==
            "borrowed" else "lent")
        entry["date"] = str(entry.get("date") or "").strip()
        entry["status"] = (
            "returned" if str(entry.get("status")) ==
            "returned" else "out")
        entry["returned_date"] = str(
            entry.get("returned_date") or "")
    return entries, broken


def borrow_log(action: str, item: str = "", person: str = "",
               date: str = "") -> str:
    """Record lending something out ('lend') or borrowing
    something in ('borrow'), mark an item back ('returned'),
    or list what's still out with the days counted."""
    action = str(action or "").strip().lower()
    if action not in ("lend", "borrow", "returned", "list"):
        return ("Borrow log actions are 'lend', 'borrow', "
                "'returned' and 'list' — e.g. borrow_log with "
                "action 'lend', the item and who has it.")
    if action == "list":
        try:
            entries, broken = _load_borrows()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the borrow log: {e}"
        out = [e for e in entries if e["status"] == "out"]
        if not out:
            base = ("Nothing is currently lent out or "
                    "borrowed.")
            if entries:
                base += (" Everything on the log has been "
                         "returned.")
            else:
                base += (" Record a loan with borrow_log, "
                         "action 'lend' or 'borrow'.")
            if broken:
                base += " " + _broken_note(broken, "borrow entry")
            return base
        today = datetime.date.today()
        out.sort(key=lambda e: e["date"])
        lines = [f"Still out ({len(out)}):"]
        for entry in out:
            days = (today - _parse_date(entry["date"])).days
            if entry["direction"] == "lent":
                who = f"lent to {entry['person']}"
            else:
                who = f"borrowed from {entry['person']}"
            lines.append(
                f"• '{entry['item']}' — {who} on "
                f"{entry['date']} ({days} day"
                f"{'s' if days != 1 else ''} out)")
        if broken:
            lines.append(_broken_note(broken, "borrow entry"))
        return "\n".join(lines)
    clean_item = _clean(item)
    if not clean_item:
        return "That needs the item's name."
    if len(clean_item) > _MAX_NAME_CHARS:
        return (f"That item name is {len(clean_item)} "
                f"characters — names are capped at "
                f"{_MAX_NAME_CHARS}.")
    if action == "returned":
        try:
            entries, _broken = _load_borrows()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the borrow log: {e}"
        clean_person = _clean(person)
        matches = [e for e in entries
                   if e["status"] == "out"
                   and e["item"].lower() == clean_item.lower()
                   and (not clean_person
                        or e["person"].lower() ==
                        clean_person.lower())]
        if not matches:
            return (f"Nothing called '{clean_item}' is "
                    "currently out"
                    + (f" with {clean_person}."
                       if clean_person else "."))
        if len(matches) > 1:
            who = ", ".join(
                f"{e['person']} ({e['direction']})"
                for e in matches)
            return (f"More than one '{clean_item}' is out — "
                    f"{who}. Tell me who it was with and I'll "
                    "mark the right one back.")
        entry = matches[0]
        today = datetime.date.today()
        days = (today - _parse_date(entry["date"])).days
        payload = {"item": entry["item"],
                   "person": entry["person"],
                   "direction": entry["direction"],
                   "date": entry["date"],
                   "status": "returned",
                   "returned_date": today.isoformat()}
        try:
            _save(_BORROW_CATEGORY, entry["_key"], payload)
        except Exception as e:  # noqa: BLE001
            return f"Couldn't mark it returned: {e}"
        return (f"Marked '{entry['item']}' as returned — it "
                f"was out for {days} day"
                f"{'s' if days != 1 else ''} "
                f"({entry['direction']} "
                f"{'to' if entry['direction'] == 'lent' else 'from'} "
                f"{entry['person']}).")
    # action == "lend" / "borrow"
    clean_person = _clean(person)
    if not clean_person:
        return ("That needs the person — who has the item"
                if action == "lend" else
                "That needs the person — who it came from.")
    if len(clean_person) > _MAX_NAME_CHARS:
        return (f"That name is {len(clean_person)} characters "
                f"— names are capped at {_MAX_NAME_CHARS}.")
    date_text = str(date or "").strip()
    if date_text:
        day = _parse_date(date_text)
        if day is None:
            return _bad_date_line(date_text)
    else:
        day = datetime.date.today()
    direction = "lent" if action == "lend" else "borrowed"
    key = (direction + " | " + clean_item.lower() + " | "
           + clean_person.lower())
    payload = {"item": clean_item, "person": clean_person,
               "direction": direction,
               "date": day.isoformat(), "status": "out",
               "returned_date": ""}
    try:
        existed = _save(_BORROW_CATEGORY, key, payload)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't record the loan: {e}"
    if direction == "lent":
        return (f"{'Updated' if existed else 'Noted'}: "
                f"'{clean_item}' lent to {clean_person} on "
                f"{day.isoformat()}.")
    return (f"{'Updated' if existed else 'Noted'}: "
            f"'{clean_item}' borrowed from {clean_person} on "
            f"{day.isoformat()}.")


# ---------------------------------------------------------------------------
# ASSET INVENTORY — things owned, with values and a total
# ---------------------------------------------------------------------------

_ASSET_CATEGORY = "asset"


def _load_assets() -> tuple[list, int]:
    """(entries, broken_count): {item, detail, value} dicts."""
    def value_ok(value) -> bool:
        return _coerce_float(value) is not None

    entries, broken = _load(_ASSET_CATEGORY, {
        "item": _nonempty, "value": value_ok})
    for entry in entries:
        entry["item"] = str(entry.get("item") or "")
        entry["detail"] = str(entry.get("detail") or "")
        entry["value"] = float(entry.get("value"))
    return entries, broken


def asset_inventory(action: str, item: str = "",
                    detail: str = "", value=0) -> str:
    """Add an asset with a value, or list the inventory with
    a total. Values are plain numbers, exactly as given."""
    action = str(action or "").strip().lower()
    if action not in ("add", "list"):
        return ("Asset inventory actions are 'add' and "
                "'list' — e.g. asset_inventory with action "
                "'add', the item and its value.")
    if action == "list":
        try:
            entries, broken = _load_assets()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the asset inventory: {e}"
        if not entries:
            base = ("The asset inventory is empty. Add "
                    "something with asset_inventory, action "
                    "'add', the item and its value.")
            if broken:
                base += " " + _broken_note(broken, "asset")
            return base
        entries.sort(key=lambda e: e["item"].lower())
        lines = [f"Asset inventory ({len(entries)}):"]
        for entry in entries:
            line = (f"• {entry['item']} — "
                    f"{_fmt_amount(entry['value'])}")
            if entry["detail"]:
                line += f" — {entry['detail']}"
            lines.append(line)
        total = sum(e["value"] for e in entries)
        lines.append(f"Total: {_fmt_amount(total)} across "
                     f"{len(entries)} item"
                     f"{'s' if len(entries) != 1 else ''}.")
        if broken:
            lines.append(_broken_note(broken, "asset"))
        return "\n".join(lines)
    # action == "add"
    clean_item = _clean(item)
    if not clean_item:
        return "Adding an asset needs the item's name."
    if len(clean_item) > _MAX_NAME_CHARS:
        return (f"That item name is {len(clean_item)} "
                f"characters — names are capped at "
                f"{_MAX_NAME_CHARS}.")
    amount = _coerce_float(value)
    if amount is None:
        return ("An asset needs a numeric value — a plain "
                "number, no currency symbol. Nothing was "
                "added.")
    if amount < 0:
        return ("An asset's value can't be below zero. "
                "Nothing was added.")
    clean_detail = _clean(detail)
    if len(clean_detail) > _MAX_TEXT_CHARS:
        return (f"That detail is {len(clean_detail)} "
                f"characters — details are capped at "
                f"{_MAX_TEXT_CHARS}.")
    payload = {"item": clean_item, "detail": clean_detail,
               "value": amount}
    try:
        existed = _save(_ASSET_CATEGORY, clean_item.lower(),
                        payload)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't add the asset: {e}"
    return (f"{'Updated' if existed else 'Added'} "
            f"'{clean_item}' to the asset inventory at a "
            f"value of {_fmt_amount(amount)}.")


# ---------------------------------------------------------------------------
# HOME INVENTORY — what an item is, which box and room hold it
# (R16-9 attic inspiration, 2026-11-11, built from scratch: the
# tree had no home-inventory / box-contents surface at all — only
# asset_inventory's bare {item, detail, value} rows, with no box,
# room, photo, condition or purchase date. One row per
# (item, box, room): adding the same item to the same box updates
# it. The photo is a path to a file that already exists — the
# inventory records the path, it never copies or opens the file.
# The re-read after an add is the A1-store proof: the handler
# claims the add only when the row is found again.)
# ---------------------------------------------------------------------------

_HOME_CATEGORY = "home_item"


def _load_home_items() -> tuple[list, int]:
    """(entries, broken_count): {item, box, room, condition, value,
    purchased, photo, detail, quantity} dicts."""
    entries, broken = _load(_HOME_CATEGORY, {"item": _nonempty})
    for entry in entries:
        entry["item"] = str(entry.get("item") or "")
        entry["box"] = str(entry.get("box") or "")
        entry["room"] = str(entry.get("room") or "")
        entry["condition"] = str(entry.get("condition") or "")
        entry["value"] = _coerce_float(entry.get("value"))
        entry["purchased"] = str(entry.get("purchased") or "")
        entry["photo"] = str(entry.get("photo") or "")
        entry["detail"] = str(entry.get("detail") or "")
        qty = entry.get("quantity")
        entry["quantity"] = qty if isinstance(qty, int) and qty >= 1 else 1
    return entries, broken


def _home_item_line(entry: dict) -> str:
    """One inventory line: name, location, condition, value, photo."""
    parts = [f"• {entry['item']}"]
    if entry["quantity"] != 1:
        parts[0] += f" ×{entry['quantity']}"
    loc = ", ".join(p for p in (entry["box"], entry["room"]) if p)
    if loc:
        parts.append(loc)
    if entry["condition"]:
        parts.append(entry["condition"])
    if entry["value"] is not None:
        parts.append(_fmt_amount(entry["value"]))
    if entry["purchased"]:
        parts.append(f"bought {entry['purchased']}")
    if entry["photo"]:
        parts.append(f"photo: {entry['photo']}")
    if entry["detail"]:
        parts.append(f"— {entry['detail']}")
    return " — ".join(parts)


def home_inventory(action: str, item: str = "", box: str = "",
                   room: str = "", condition: str = "", value=None,
                   purchased: str = "", photo: str = "",
                   detail: str = "", quantity: int = 1) -> str:
    """Keep a home inventory: what an item is, which box and room
    hold it, its condition, value, purchase date and a photo.
    Actions: 'add' stores an item, 'list' shows everything with
    its location, 'find' searches by item, box or room. Records
    persist as memory facts. One row per (item, box, room) —
    adding the same item to the same box updates it; the photo is
    a path to a file that already exists. Use when the user asks
    what's in a box, where something is stored, or what they own
    for an insurance claim."""
    action = str(action or "").strip().lower()
    if action not in ("add", "list", "find"):
        return ("Home inventory actions are 'add', 'list' and "
                "'find' — e.g. home_inventory with action 'add', "
                "the item, its box and its room.")
    if action == "list":
        try:
            entries, broken = _load_home_items()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the home inventory: {e}"
        if not entries:
            base = ("The home inventory is empty. Add something "
                    "with home_inventory, action 'add', the item, "
                    "its box and its room.")
            if broken:
                base += " " + _broken_note(broken, "home item")
            return base
        entries.sort(key=lambda e: (e["room"].lower(), e["box"].lower(),
                                    e["item"].lower()))
        lines = [f"Home inventory ({len(entries)}):"]
        lines.extend(_home_item_line(e) for e in entries)
        valued = [e for e in entries if e["value"] is not None]
        if valued:
            total = sum(e["value"] * e["quantity"] for e in valued)
            lines.append(f"Total: {_fmt_amount(total)} across "
                         f"{len(valued)} valued item"
                         f"{'s' if len(valued) != 1 else ''}.")
        if broken:
            lines.append(_broken_note(broken, "home item"))
        return "\n".join(lines)
    if action == "find":
        query = _clean(item)
        want_box = _clean(box)
        want_room = _clean(room)
        if not query and not want_box and not want_room:
            return ("Finding needs something to look for — an item "
                    "name, a box, or a room.")
        try:
            entries, broken = _load_home_items()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the home inventory: {e}"
        hits = [e for e in entries
                if (not query or query.lower() in e["item"].lower())
                and (not want_box
                     or want_box.lower() in e["box"].lower())
                and (not want_room
                     or want_room.lower() in e["room"].lower())]
        if not hits:
            return ("Nothing in the home inventory matches that — "
                    "try 'list' to see everything stored.")
        hits.sort(key=lambda e: (e["room"].lower(), e["box"].lower(),
                                 e["item"].lower()))
        lines = [f"Found {len(hits)}:"]
        lines.extend(_home_item_line(e) for e in hits)
        if broken:
            lines.append(_broken_note(broken, "home item"))
        return "\n".join(lines)
    # action == "add"
    clean_item = _clean(item)
    if not clean_item:
        return "Adding to the home inventory needs the item's name."
    if len(clean_item) > _MAX_NAME_CHARS:
        return (f"That item name is {len(clean_item)} characters — "
                f"names are capped at {_MAX_NAME_CHARS}.")
    clean_box = _clean(box)
    clean_room = _clean(room)
    clean_condition = _clean(condition)
    for label, text in (("box", clean_box), ("room", clean_room),
                        ("condition", clean_condition)):
        if len(text) > _MAX_NAME_CHARS:
            return (f"That {label} is {len(text)} characters — "
                    f"names are capped at {_MAX_NAME_CHARS}.")
    if value in ("", None):
        amount = None
    else:
        amount = _coerce_float(value)
        if amount is None:
            return ("An item's value needs to be a plain number — "
                    "no currency symbol. Nothing was added.")
        if amount < 0:
            return ("An item's value can't be below zero. Nothing "
                    "was added.")
    clean_purchased = _clean(purchased)
    if clean_purchased and _parse_date(clean_purchased) is None:
        return _bad_date_line(clean_purchased)
    clean_photo = _clean(photo)
    if clean_photo and not os.path.isfile(clean_photo):
        return (f"No file at '{clean_photo}' — the photo must be "
                "an existing file. Nothing was added.")
    clean_detail = _clean(detail)
    if len(clean_detail) > _MAX_TEXT_CHARS:
        return (f"That detail is {len(clean_detail)} characters — "
                f"details are capped at {_MAX_TEXT_CHARS}.")
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        return ("Quantity needs to be a whole number, 1 or more. "
                "Nothing was added.")
    if qty < 1:
        return ("Quantity needs to be 1 or more. Nothing was "
                "added.")
    key = "|".join((clean_room, clean_box, clean_item)).lower()
    payload = {"item": clean_item, "box": clean_box, "room": clean_room,
               "condition": clean_condition, "value": amount,
               "purchased": clean_purchased, "photo": clean_photo,
               "detail": clean_detail, "quantity": qty}
    try:
        existed = _save(_HOME_CATEGORY, key, payload)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't add the item: {e}"
    # A1-store: re-read the row and claim the add only when it
    # is found again.
    try:
        entries, _broken = _load_home_items()
    except Exception:  # noqa: BLE001
        return ("The item may not have saved — the inventory "
                "couldn't be re-read. Nothing is claimed.")
    landed = any(e.get("_key") == key for e in entries)
    if not landed:
        return ("The item did not land in the inventory — nothing "
                "was stored. Try again.")
    where = ", ".join(p for p in (clean_box, clean_room) if p)
    return (f"{'Updated' if existed else 'Added'} '{clean_item}'"
            f"{' in ' + where if where else ''}."
            f"{' Photo: ' + clean_photo if clean_photo else ''}")


# ---------------------------------------------------------------------------
# WAREHOUSE STOCK — a small warehouse / storage-unit surface
# (R16-10 GreaterWMS inspiration, 2026-11-11, built from scratch: the
# tree had no warehouse surface at all — no stock locations, no
# inbound/outbound, no unit occupancy. home_inventory tracks what
# an item is and which box/room holds it, but it has no quantities
# that move between locations, no receive/ship flow, no capacity
# and no movement history, so a storage unit cannot be run on it.
# One stock row per (item, location); locations carry a capacity
# in units; every receive/ship/move is appended to a movement
# ledger, so the list can show what moved lately. The re-read
# after a write is the A1-store proof: the handler claims a
# movement only when the stock row is found again with the
# expected quantity. Ship and move never take stock below zero —
# a shortfall is refused honestly, never silently clipped. No
# billing, no staff roles, no customer portal, no hosted
# dashboard — those need owner rulings; this is one operator's
# own units, local only.)
# ---------------------------------------------------------------------------

_WAREHOUSE_STOCK_CATEGORY = "warehouse_stock"
_WAREHOUSE_LOCATION_CATEGORY = "warehouse_location"
_WAREHOUSE_MOVE_CATEGORY = "warehouse_move"


def _load_warehouse_stock() -> tuple[list, int]:
    """(entries, broken_count): {item, location, quantity, detail} dicts."""
    entries, broken = _load(_WAREHOUSE_STOCK_CATEGORY, {
        "item": _nonempty, "location": _nonempty})
    for entry in entries:
        entry["item"] = str(entry.get("item") or "")
        entry["location"] = str(entry.get("location") or "")
        entry["detail"] = str(entry.get("detail") or "")
        qty = entry.get("quantity")
        entry["quantity"] = qty if isinstance(qty, int) and qty >= 0 else 0
    return entries, broken


def _load_warehouse_locations() -> tuple[list, int]:
    """(entries, broken_count): {location, capacity} dicts."""
    entries, broken = _load(_WAREHOUSE_LOCATION_CATEGORY, {
        "location": _nonempty})
    for entry in entries:
        entry["location"] = str(entry.get("location") or "")
        cap = entry.get("capacity")
        entry["capacity"] = cap if isinstance(cap, int) and cap >= 1 else None
    return entries, broken


def _load_warehouse_moves() -> tuple[list, int]:
    """(entries, broken_count): {item, kind, from_location,
    to_location, quantity, time} dicts, oldest first."""
    entries, broken = _load(_WAREHOUSE_MOVE_CATEGORY, {
        "item": _nonempty, "kind": _nonempty})
    for entry in entries:
        entry["item"] = str(entry.get("item") or "")
        entry["kind"] = str(entry.get("kind") or "")
        entry["from_location"] = str(entry.get("from_location") or "")
        entry["to_location"] = str(entry.get("to_location") or "")
        entry["time"] = str(entry.get("time") or "")
        qty = entry.get("quantity")
        entry["quantity"] = qty if isinstance(qty, int) and qty >= 0 else 0
    entries.sort(key=lambda e: e["time"])
    return entries, broken


def _warehouse_qty(value):
    """Whole number >= 1, or None when the value is not one.
    Floats are refused, not truncated — 2.9 is not 2 units."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        qty = value
    elif isinstance(value, float):
        return None
    else:
        try:
            qty = int(str(value).strip())
        except (TypeError, ValueError):
            return None
    return qty if qty >= 1 else None


def _warehouse_stock_line(entry: dict) -> str:
    """One stock line: item, location, quantity on hand."""
    line = f"• {entry['item']} — {entry['location']} — "
    line += f"{entry['quantity']} on hand"
    if entry["detail"]:
        line += f" — {entry['detail']}"
    return line


def _warehouse_move_line(entry: dict) -> str:
    """One ledger line: when, what moved, how many, where."""
    when = entry["time"][:19].replace("T", " ")
    qty = f"{entry['quantity']} × {entry['item']}"
    if entry["kind"] == "receive":
        return f"• {when} — received {qty} at {entry['to_location']}"
    if entry["kind"] == "ship":
        return f"• {when} — shipped {qty} from {entry['from_location']}"
    return (f"• {when} — moved {qty} from "
            f"{entry['from_location']} to {entry['to_location']}")


def _warehouse_record_move(kind: str, item: str, from_location: str,
                           to_location: str, quantity: int) -> None:
    """Append one movement to the ledger. The key carries the
    microsecond timestamp plus the full movement, so two
    identical moves in the same microsecond still land apart
    only if every field matches — practically never, and a
    collision would overwrite one ledger line, never stock."""
    now = datetime.datetime.now().isoformat(timespec="microseconds")
    key = "|".join((now, kind, item, from_location,
                    to_location, str(quantity))).lower()
    _save(_WAREHOUSE_MOVE_CATEGORY, key, {
        "item": item, "kind": kind, "from_location": from_location,
        "to_location": to_location, "quantity": quantity,
        "time": now})


def warehouse_stock(action: str = "", item: str = "", location: str = "",
                    from_location: str = "", to_location: str = "",
                    quantity: int = 1, capacity=None,
                    detail: str = "") -> str:
    """Run a small warehouse: stock locations with quantities,
    inbound (receive), outbound (ship), moves between locations,
    and per-location occupancy. Actions: 'receive' adds stock to
    a location (capacity sets that location's capacity in units,
    new locations take it, existing ones keep theirs unless a
    new capacity is given); 'ship' takes stock out and refuses
    when there is not enough on hand; 'move' transfers stock
    between locations and refuses a shortfall at the source;
    'list' shows stock by location with totals and recent
    movements; 'find' searches by item or location; 'occupancy'
    shows each location's fill against its capacity. One stock
    row per (item, location); every movement is written to a
    ledger. Records persist as memory facts. Use when the user
    asks what's in a unit, where stock is, how full a unit is,
    or to receive, ship or move stock."""
    action = str(action or "").strip().lower()
    if action not in ("receive", "ship", "move", "list", "find",
                      "occupancy"):
        return ("Warehouse stock actions are 'receive', 'ship', "
                "'move', 'list', 'find' and 'occupancy' — e.g. "
                "warehouse_stock with action 'receive', the item, "
                "its location and a quantity.")
    if action == "list":
        try:
            entries, broken = _load_warehouse_stock()
            moves, _mbroken = _load_warehouse_moves()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the warehouse stock: {e}"
        want = _clean(location)
        if want:
            entries = [e for e in entries
                       if want.lower() in e["location"].lower()]
        if not entries:
            base = ("The warehouse is empty" +
                    (f" at '{want}'" if want else "") +
                    ". Receive stock with warehouse_stock, action "
                    "'receive', the item, its location and a "
                    "quantity.")
            if broken:
                base += " " + _broken_note(broken, "stock row")
            return base
        entries.sort(key=lambda e: (e["location"].lower(),
                                    e["item"].lower()))
        lines = [f"Warehouse stock ({len(entries)} row"
                 f"{'s' if len(entries) != 1 else ''}):"]
        lines.extend(_warehouse_stock_line(e) for e in entries)
        units = sum(e["quantity"] for e in entries)
        places = len({e["location"].lower() for e in entries})
        lines.append(f"Total: {units} units across "
                     f"{len(entries)} row"
                     f"{'s' if len(entries) != 1 else ''} in "
                     f"{places} location"
                     f"{'s' if places != 1 else ''}.")
        if moves:
            lines.append("Recent movements:")
            lines.extend(_warehouse_move_line(m)
                         for m in moves[-5:][::-1])
        if broken:
            lines.append(_broken_note(broken, "stock row"))
        return "\n".join(lines)
    if action == "find":
        query = _clean(item)
        want = _clean(location)
        if not query and not want:
            return ("Finding needs something to look for — an item "
                    "name or a location.")
        try:
            entries, broken = _load_warehouse_stock()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the warehouse stock: {e}"
        hits = [e for e in entries
                if (not query or query.lower() in e["item"].lower())
                and (not want
                     or want.lower() in e["location"].lower())]
        if not hits:
            return ("Nothing in the warehouse matches that — try "
                    "'list' to see everything stored.")
        hits.sort(key=lambda e: (e["location"].lower(),
                                 e["item"].lower()))
        lines = [f"Found {len(hits)}:"]
        lines.extend(_warehouse_stock_line(e) for e in hits)
        if broken:
            lines.append(_broken_note(broken, "stock row"))
        return "\n".join(lines)
    if action == "occupancy":
        try:
            entries, _broken = _load_warehouse_stock()
            locations, _lbroken = _load_warehouse_locations()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the warehouse stock: {e}"
        caps = {e["location"].lower(): e for e in locations}
        used: dict[str, int] = {}
        names: dict[str, str] = {}
        for e in entries:
            key = e["location"].lower()
            used[key] = used.get(key, 0) + e["quantity"]
            names[key] = e["location"]
        for key, loc in caps.items():
            used.setdefault(key, 0)
            names[key] = loc["location"]
        if not names:
            return ("No warehouse locations yet. Receive stock "
                    "with warehouse_stock, action 'receive', to "
                    "create the first location.")
        lines = ["Warehouse occupancy:"]
        total_used = 0
        for key in sorted(names):
            total_used += used[key]
            cap = caps.get(key, {}).get("capacity")
            if cap:
                pct = used[key] * 100 // cap
                lines.append(f"• {names[key]} — {used[key]} of "
                             f"{cap} units ({pct}% full)")
            else:
                lines.append(f"• {names[key]} — {used[key]} units "
                             f"(capacity not set)")
        lines.append(f"Total: {total_used} units across "
                     f"{len(names)} location"
                     f"{'s' if len(names) != 1 else ''}.")
        return "\n".join(lines)
    # The three writing actions share validation and the A1-store
    # re-read: claim a movement only when the stock row is found
    # again with the expected quantity.
    clean_item = _clean(item)
    if not clean_item:
        return ("Warehouse stock needs the item's name for "
                f"'{action}'.")
    if len(clean_item) > _MAX_NAME_CHARS:
        return (f"That item name is {len(clean_item)} characters — "
                f"names are capped at {_MAX_NAME_CHARS}.")
    qty = _warehouse_qty(quantity)
    if qty is None:
        return ("Quantity needs to be a whole number, 1 or more. "
                "Nothing was moved.")
    clean_detail = _clean(detail)
    if len(clean_detail) > _MAX_TEXT_CHARS:
        return (f"That detail is {len(clean_detail)} characters — "
                f"details are capped at {_MAX_TEXT_CHARS}.")
    if action == "receive":
        clean_location = _clean(location)
        if not clean_location:
            return "Receiving needs the location the stock goes to."
        if len(clean_location) > _MAX_NAME_CHARS:
            return (f"That location is {len(clean_location)} "
                    f"characters — names are capped at "
                    f"{_MAX_NAME_CHARS}.")
        cap = None
        if capacity not in ("", None):
            cap = _warehouse_qty(capacity)
            if cap is None:
                return ("Capacity needs to be a whole number, 1 or "
                        "more. Nothing was received.")
        try:
            entries, _broken = _load_warehouse_stock()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the warehouse stock: {e}"
        key = "|".join((clean_location, clean_item)).lower()
        old = next((e for e in entries if e.get("_key") == key), None)
        new_qty = (old["quantity"] if old else 0) + qty
        payload = {"item": clean_item, "location": clean_location,
                   "quantity": new_qty,
                   "detail": clean_detail or (old["detail"] if old
                                              else "")}
        try:
            _save(_WAREHOUSE_STOCK_CATEGORY, key, payload)
            if cap is not None:
                _save(_WAREHOUSE_LOCATION_CATEGORY,
                      clean_location.lower(),
                      {"location": clean_location, "capacity": cap})
            _warehouse_record_move("receive", clean_item, "",
                                   clean_location, qty)
        except Exception as e:  # noqa: BLE001
            return f"Couldn't receive the stock: {e}"
        try:
            entries, _broken = _load_warehouse_stock()
        except Exception:  # noqa: BLE001
            return ("The stock may not have saved — the warehouse "
                    "couldn't be re-read. Nothing is claimed.")
        landed = next((e for e in entries if e.get("_key") == key),
                      None)
        if landed is None or landed["quantity"] != new_qty:
            return ("The stock did not land in the warehouse — "
                    "nothing was stored. Try again.")
        result = (f"Received {qty} × '{clean_item}' at "
                  f"{clean_location} ({new_qty} on hand now).")
        if cap is not None:
            result += f" Capacity set to {cap} units."
        return result
    if action == "ship":
        clean_location = _clean(location)
        if not clean_location:
            return "Shipping needs the location the stock leaves."
        if len(clean_location) > _MAX_NAME_CHARS:
            return (f"That location is {len(clean_location)} "
                    f"characters — names are capped at "
                    f"{_MAX_NAME_CHARS}.")
        try:
            entries, _broken = _load_warehouse_stock()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the warehouse stock: {e}"
        key = "|".join((clean_location, clean_item)).lower()
        row = next((e for e in entries if e.get("_key") == key), None)
        if row is None:
            return (f"No '{clean_item}' stored at {clean_location} "
                    "— nothing was shipped.")
        if qty > row["quantity"]:
            return (f"Only {row['quantity']} × '{clean_item}' on "
                    f"hand at {clean_location} — nothing was "
                    "shipped.")
        new_qty = row["quantity"] - qty
        try:
            _save(_WAREHOUSE_STOCK_CATEGORY, key, {
                "item": clean_item, "location": clean_location,
                "quantity": new_qty, "detail": row["detail"]})
            _warehouse_record_move("ship", clean_item,
                                   clean_location, "", qty)
        except Exception as e:  # noqa: BLE001
            return f"Couldn't ship the stock: {e}"
        try:
            entries, _broken = _load_warehouse_stock()
        except Exception:  # noqa: BLE001
            return ("The stock may not have shipped — the "
                    "warehouse couldn't be re-read. Nothing is "
                    "claimed.")
        landed = next((e for e in entries if e.get("_key") == key),
                      None)
        if landed is None or landed["quantity"] != new_qty:
            return ("The shipment did not land in the warehouse — "
                    "nothing was stored. Try again.")
        return (f"Shipped {qty} × '{clean_item}' from "
                f"{clean_location} ({new_qty} left).")
    # action == "move"
    clean_from = _clean(from_location)
    clean_to = _clean(to_location)
    if not clean_from or not clean_to:
        return ("Moving needs both the location the stock leaves "
                "and the location it goes to.")
    if len(clean_from) > _MAX_NAME_CHARS or \
            len(clean_to) > _MAX_NAME_CHARS:
        return ("Locations are capped at "
                f"{_MAX_NAME_CHARS} characters.")
    if clean_from.lower() == clean_to.lower():
        return ("Moving within the same location changes nothing "
                "— nothing was moved.")
    try:
        entries, _broken = _load_warehouse_stock()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the warehouse stock: {e}"
    src_key = "|".join((clean_from, clean_item)).lower()
    dst_key = "|".join((clean_to, clean_item)).lower()
    src = next((e for e in entries if e.get("_key") == src_key), None)
    if src is None:
        return (f"No '{clean_item}' stored at {clean_from} — "
                "nothing was moved.")
    if qty > src["quantity"]:
        return (f"Only {src['quantity']} × '{clean_item}' on hand "
                f"at {clean_from} — nothing was moved.")
    dst = next((e for e in entries if e.get("_key") == dst_key), None)
    new_src = src["quantity"] - qty
    new_dst = (dst["quantity"] if dst else 0) + qty
    try:
        _save(_WAREHOUSE_STOCK_CATEGORY, src_key, {
            "item": clean_item, "location": clean_from,
            "quantity": new_src, "detail": src["detail"]})
        _save(_WAREHOUSE_STOCK_CATEGORY, dst_key, {
            "item": clean_item, "location": clean_to,
            "quantity": new_dst,
            "detail": (dst["detail"] if dst else src["detail"])})
        _warehouse_record_move("move", clean_item, clean_from,
                               clean_to, qty)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't move the stock: {e}"
    try:
        entries, _broken = _load_warehouse_stock()
    except Exception:  # noqa: BLE001
        return ("The stock may not have moved — the warehouse "
                "couldn't be re-read. Nothing is claimed.")
    landed_src = next((e for e in entries
                       if e.get("_key") == src_key), None)
    landed_dst = next((e for e in entries
                       if e.get("_key") == dst_key), None)
    if landed_src is None or landed_src["quantity"] != new_src or \
            landed_dst is None or landed_dst["quantity"] != new_dst:
        return ("The move did not land in the warehouse — nothing "
                "was stored. Try again.")
    return (f"Moved {qty} × '{clean_item}' from {clean_from} to "
            f"{clean_to} ({new_src} left at {clean_from}, "
            f"{new_dst} at {clean_to}).")
