"""Life-admin tools of the Productivity-3 pack.

Phase A batch A29 part 2 (skills split): moved verbatim from
core/skills_productivity_day.py — inbox_zero_assist,
unsubscribe_finder, bill_calendar, shopping_list, packing_list
and birthday_radar, plus the helpers only they use. The
focus-day family lives in skills_productivity3_focus_day.
Behaviour unchanged.
"""

import datetime
import json
import re
import urllib.parse
import urllib.request
from email.utils import parseaddr

from . import google_services, injection
from .memory.memory import memory_store  # Phase 3 M2h: memory.py moved to core/memory/memory.py
# INTENTIONAL (split A29 part 2): _coerce_int is shared by both
# families, so it lives in skills_productivity3_helpers (plan
# P3(c)); this module imports it rather than duplicating it.
from .skills_productivity3_helpers import _coerce_int


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"

# ---------------------------------------------------------------------------
# Seams — every external read sits behind one of these so tests can
# monkeypatch a single name instead of a whole subsystem.
# ---------------------------------------------------------------------------

def _all_facts() -> list:
    """Every stored memory fact ({category, key, value, ...})."""
    return memory_store.get_all_facts()

def _gmail_service():
    """(service, failure_message) for Gmail."""
    return google_services._service_or_failure("gmail", "your mail")

def _mail_headers(query: str, limit: int):
    """(messages, failure): one dict per message carrying sender
    name/address, subject, snippet and the List-Unsubscribe header
    — the metadata the two inbox tools sort by. Exactly one of the
    pair is meaningful."""
    svc, failure = _gmail_service()
    if svc is None:
        return None, failure
    try:
        listing = google_services._list_messages(svc, query, limit)
    except Exception as e:  # noqa: BLE001
        return None, f"Couldn't read your mail just now ({e})."
    out = []
    for stub in listing.get("messages") or []:
        try:
            msg = (
                svc.users()
                .messages()
                .get(userId="me", id=stub.get("id"), format="metadata")
                .execute()
            )
        except Exception:  # noqa: BLE001 — one bad message sinks nothing
            continue
        headers = (msg.get("payload") or {}).get("headers") or []
        from_value = google_services._header(headers, "From")
        out.append({
            "sender": google_services._sender_name(from_value),
            "address": parseaddr(from_value or "")[1].lower(),
            "subject": (google_services._decode_mime(
                google_services._header(headers, "Subject")).strip()
                or "(no subject)"),
            "snippet": " ".join((msg.get("snippet") or "").split()),
            "unsubscribe": google_services._header(
                headers, "List-Unsubscribe"),
        })
    return out, ""

_WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy",
    3: "Overcast", 45: "Foggy", 48: "Icy fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Heavy drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Slight showers", 81: "Moderate showers",
    82: "Violent showers", 85: "Slight snow showers",
    86: "Heavy snow showers", 95: "Thunderstorm",
    96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}

_RAIN_CODES = {51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99}

_SNOW_CODES = {71, 73, 75, 77, 85, 86}

def _weather_now(place: str):
    """Current weather for a place name, via Open-Meteo (free, no
    key — the same service core/funnel/tools_handlers.py's weather tool uses):
    {condition, code, temp_c, wind_kmh} or None when the place
    can't be found or the fetch fails."""
    try:
        geo_url = (
            "https://geocoding-api.open-meteo.com/v1/search?name="
            + urllib.parse.quote_plus(place)
            + "&count=1&language=en&format=json"
        )
        with urllib.request.urlopen(geo_url, timeout=6) as r:
            geo = json.loads(r.read().decode())
        results = geo.get("results") or []
        if not results:
            return None
        lat = results[0]["latitude"]
        lon = results[0]["longitude"]
        wx_url = (
            "https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            "&current=temperature_2m,wind_speed_10m,weathercode"
            "&forecast_days=1"
        )
        with urllib.request.urlopen(wx_url, timeout=6) as r:
            wx = json.loads(r.read().decode())
        cur = wx.get("current") or {}
        code = cur.get("weathercode")
        temp = cur.get("temperature_2m")
        if code is None or temp is None:
            return None
        return {
            "condition": _WMO_CODES.get(int(code), "Unknown conditions"),
            "code": int(code),
            "temp_c": float(temp),
            "wind_kmh": float(cur.get("wind_speed_10m") or 0),
        }
    except Exception:  # noqa: BLE001 — weather is best-effort colour
        return None

# ---------------------------------------------------------------------------
# INBOX TRIAGE — heuristic buckets over the unread list (read-only)
# ---------------------------------------------------------------------------

_BULK_SENDERS = ("noreply", "no-reply", "donotreply", "do-not-reply",
                 "newsletter", "marketing", "promo", "deals",
                 "notifications", "updates")

_MARKETING_WORDS = ("sale", "% off", "discount", "coupon", "deal",
                    "offer ends", "last chance", "free shipping",
                    "shop now", "promo code", "flash sale")

_REPLY_PHRASES = ("please reply", "please respond", "let me know",
                  "can you", "could you", "would you", "rsvp",
                  "action required", "please review",
                  "your approval", "get back to me",
                  "please confirm", "waiting for your")

def _bucket_of(item: dict) -> str:
    subject = item["subject"].lower()
    snippet = item["snippet"].lower()
    address = item["address"]
    bulk = bool(item["unsubscribe"])
    marketing = any(w in subject or w in snippet
                    for w in _MARKETING_WORDS)
    if bulk and marketing:
        return "noise"
    if any(p in subject or p in snippet for p in _REPLY_PHRASES):
        return "reply"
    if not bulk and "?" in item["subject"]:
        return "reply"
    if bulk or any(s in address for s in _BULK_SENDERS):
        return "noise" if marketing else "fyi"
    return "fyi"

_UNSUB_URL_RE = re.compile(r"<(https?://[^>]+)>")

_UNSUB_MAILTO_RE = re.compile(r"<(mailto:[^>]+)>")

def _unsub_mechanism(header_value: str) -> str:
    urls = _UNSUB_URL_RE.findall(header_value or "")
    if urls:
        return f"one-click link: {urls[0]}"
    mailtos = _UNSUB_MAILTO_RE.findall(header_value or "")
    if mailtos:
        address = mailtos[0][len("mailto:"):].split("?")[0]
        return f"unsubscribe by email: {address}"
    return ""

# ---------------------------------------------------------------------------
# BILL CALENDAR — recurring expenses as memory facts
# ---------------------------------------------------------------------------

_BILLS_CATEGORY = "bills"

def _load_bills() -> tuple[list, int]:
    """(entries, broken_count): {name, amount, day} dicts parsed
    from the bills facts; unparseable ones are counted, not fatal."""
    entries = []
    broken = 0
    for fact in _all_facts():
        if fact.get("category") != _BILLS_CATEGORY:
            continue
        try:
            stored = json.loads(fact.get("value") or "")
            amount = float(stored["amount"])
            day = int(stored["day"])
            if amount <= 0 or not 1 <= day <= 31:
                raise ValueError
            entries.append({"name": str(fact.get("key", "")),
                            "amount": amount, "day": day})
        except Exception:  # noqa: BLE001
            broken += 1
    entries.sort(key=lambda e: (e["day"], e["name"]))
    return entries, broken

# ---------------------------------------------------------------------------
# SHOPPING LIST — one persistent spoken list, as a memory fact
# ---------------------------------------------------------------------------

_LISTS_CATEGORY = "lists"

_SHOPPING_KEY = "shopping"

_MAX_SHOPPING_ITEMS = 100

_MAX_ITEM_CHARS = 80

def _load_shopping() -> list:
    fact = memory_store.get_fact(_LISTS_CATEGORY, _SHOPPING_KEY)
    if not fact:
        return []
    try:
        stored = json.loads(fact.get("value") or "")
    except Exception:  # noqa: BLE001
        return []
    if isinstance(stored, list):
        return [str(v) for v in stored if isinstance(v, str)
                and v.strip()]
    return []

def _save_shopping(items: list) -> None:
    memory_store.save_fact(_LISTS_CATEGORY, _SHOPPING_KEY,
                           json.dumps(items))

# ---------------------------------------------------------------------------
# BIRTHDAY RADAR — stored birthdays falling in the next 14 days
# ---------------------------------------------------------------------------

_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3,
    "march": 3, "apr": 4, "april": 4, "may": 5, "jun": 6,
    "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_BIRTHDAY_WINDOW_DAYS = 14

def _parse_birthday(text: str):
    """(month, day) from a stored fact's text, or None.

    Reads ISO dates (1990-03-05), '5 March' / 'March 5' (with or
    without a year or ordinal suffix) and dd/mm/yyyy."""
    t = (text or "").lower()
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if match:
        month, day = int(match.group(2)), int(match.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return month, day
    match = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\b", t)
    if match and match.group(2) in _MONTH_NAMES:
        day = int(match.group(1))
        if 1 <= day <= 31:
            return _MONTH_NAMES[match.group(2)], day
    match = re.search(
        r"\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
    if match and match.group(1) in _MONTH_NAMES:
        day = int(match.group(2))
        if 1 <= day <= 31:
            return _MONTH_NAMES[match.group(1)], day
    match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", t)
    if match:
        first, second = int(match.group(1)), int(match.group(2))
        # dd/mm/yyyy unless the numbers force the other reading.
        day, month = (first, second) if first <= 12 or second > 12 \
            else (second, first)
        if 1 <= month <= 12 and 1 <= day <= 31:
            return month, day
    return None

def _birthday_name(fact: dict) -> str:
    key = str(fact.get("key") or "")
    name = re.sub(r"birthday", "", key, flags=re.IGNORECASE)
    name = name.replace("_", " ").replace("-", " ")
    name = re.sub(r"'s\b", "", name).strip(" '’")
    if name:
        return name.title()
    return "A contact"

def inbox_zero_assist(limit: int = 20) -> str:
    """A rough needs-reply / FYI / noise sort of the unread mail."""
    limit = _coerce_int(limit, 20, 1, 50)
    items, failure = _mail_headers("is:unread", limit)
    if items is None:
        return failure
    if not items:
        return "Inbox zero — no unread email to sort."
    buckets = {"reply": [], "fyi": [], "noise": []}
    for item in items:
        buckets[_bucket_of(item)].append(item)
    head = (f"Rough sort of your {len(items)} unread email"
            f"{'s' if len(items) != 1 else ''} — the buckets are "
            "a heuristic first pass from sender and subject "
            "patterns, not gospel:")
    counts = [
        head,
        f"• Needs a reply: {len(buckets['reply'])}",
        f"• For your information: {len(buckets['fyi'])}",
        f"• Likely noise: {len(buckets['noise'])}",
    ]
    examples = []
    for key, label in (("reply", "Needs a reply"),
                       ("fyi", "For your information"),
                       ("noise", "Likely noise")):
        for item in buckets[key][:2]:
            examples.append(
                f"{label} — e.g. {item['sender']}: "
                f"{item['subject']}")
    result = "\n".join(counts)
    if examples:
        result += "\n" + injection.wrap_untrusted(
            "email", "\n".join(examples))
    return result

def unsubscribe_finder(limit: int = 25) -> str:
    """List recent senders offering a List-Unsubscribe way out."""
    limit = _coerce_int(limit, 25, 1, 50)
    items, failure = _mail_headers("in:inbox", limit)
    if items is None:
        return failure
    seen = set()
    found = []
    for item in items:
        if not item["unsubscribe"]:
            continue
        mechanism = _unsub_mechanism(item["unsubscribe"])
        if not mechanism:
            continue
        key = item["address"] or item["sender"].lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(f"• {item['sender']} — {mechanism}")
    if not found:
        return (f"No List-Unsubscribe headers in your "
                f"{len(items)} most recent inbox emails — those "
                "senders offer no easy way out, or the mail is "
                "all personal.")
    shown = found[:15]
    result = (f"{len(found)} sender"
              f"{'s' if len(found) != 1 else ''} in your recent "
              "mail offer an unsubscribe route:")
    result += "\n" + injection.wrap_untrusted(
        "email", "\n".join(shown))
    if len(found) > len(shown):
        result += (f"\n(and {len(found) - len(shown)} more "
                   "not shown)")
    result += ("\nListing only — I haven't unsubscribed you "
               "from anything.")
    return result

def bill_calendar(action: str, name: str = "", amount: float = 0,
                  day_of_month: int = 1) -> str:
    """Add, list or remove a recurring monthly expense."""
    action = str(action or "").strip().lower()
    if action not in ("add", "list", "remove"):
        return ("Bill actions are 'add', 'list' and 'remove' — "
                "e.g. bill_calendar with action 'add', a name, an "
                "amount and a day of the month.")
    if action == "list":
        entries, broken = _load_bills()
        if not entries:
            base = ("No recurring bills stored yet. Add one with "
                    "action 'add', a name, an amount and the day "
                    "of the month it lands.")
            if broken:
                base += (f" ({broken} stored bill"
                         f"{'s' if broken != 1 else ''} couldn't "
                         "be read and were skipped.)")
            return base
        lines = [f"Recurring bills ({len(entries)}):"]
        total = 0.0
        for entry in entries:
            total += entry["amount"]
            lines.append(
                f"• {entry['name']} — {entry['amount']:,.2f} on "
                f"the {_ordinal(entry['day'])} of each month")
        lines.append(f"Monthly total: {total:,.2f} across "
                     f"{len(entries)} bill"
                     f"{'s' if len(entries) != 1 else ''}.")
        if broken:
            lines.append(f"({broken} stored bill"
                         f"{'s' if broken != 1 else ''} couldn't "
                         "be read and were skipped.)")
        return "\n".join(lines)
    clean = str(name or "").strip()
    if not clean:
        return f"A bill {action} needs its name."
    key = clean.lower()
    if action == "remove":
        try:
            fact = memory_store.get_fact(_BILLS_CATEGORY, key)
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the bill list: {e}"
        if not fact:
            entries, _broken = _load_bills()
            known = (", ".join(e["name"] for e in entries)
                     if entries else "")
            extra = (f" Stored bills: {known}." if known
                     else " No bills are stored.")
            return f"There's no bill called '{clean}'.{extra}"
        try:
            memory_store.delete_fact(int(fact["id"]))
        except Exception as e:  # noqa: BLE001
            return f"Couldn't remove bill '{clean}': {e}"
        return f"Removed the recurring bill '{clean}'."
    # action == "add"
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return "A bill needs a numeric amount — e.g. 499 or 1299.50."
    if value <= 0:
        return "A bill's amount must be more than zero."
    day = _coerce_int(day_of_month, 1, 1, 31)
    if int(day_of_month or 0) != day:
        return ("The day of the month must be a whole number "
                "from 1 to 31.")
    try:
        existed = memory_store.get_fact(_BILLS_CATEGORY,
                                        key) is not None
        memory_store.save_fact(
            _BILLS_CATEGORY, key,
            json.dumps({"amount": value, "day": day}))
    except Exception as e:  # noqa: BLE001
        return f"Couldn't save the bill: {e}"
    result = (f"{'Updated' if existed else 'Added'} bill "
              f"'{clean}': {value:,.2f} on the {_ordinal(day)} "
              "of each month.")
    return result

def shopping_list(action: str = "read", item: str = "") -> str:
    """Add to, remove from, read or clear the shopping list."""
    action = str(action or "read").strip().lower()
    if action not in ("add", "remove", "read", "clear"):
        return ("Shopping list actions are 'add', 'remove', "
                "'read' and 'clear'.")
    try:
        items = _load_shopping()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the shopping list: {e}"
    if action == "read":
        if not items:
            return ("Your shopping list is empty. Add to it with "
                    "shopping_list, action 'add' and an item.")
        lines = [f"Your shopping list ({len(items)} item"
                 f"{'s' if len(items) != 1 else ''}):"]
        lines += [f"{i}. {entry}"
                  for i, entry in enumerate(items, 1)]
        return "\n".join(lines)
    if action == "clear":
        if not items:
            return "Your shopping list is already empty."
        count = len(items)
        try:
            fact = memory_store.get_fact(_LISTS_CATEGORY,
                                         _SHOPPING_KEY)
            if fact:
                memory_store.delete_fact(int(fact["id"]))
        except Exception as e:  # noqa: BLE001
            return f"Couldn't clear the shopping list: {e}"
        return (f"Cleared the shopping list — {count} item"
                f"{'s' if count != 1 else ''} removed.")
    clean = str(item or "").strip()
    if not clean:
        return f"Adding to or removing from the list needs the item."
    if action == "add":
        if len(clean) > _MAX_ITEM_CHARS:
            return (f"That item is {len(clean)} characters — "
                    f"list items are capped at {_MAX_ITEM_CHARS}.")
        for existing in items:
            if existing.lower() == clean.lower():
                return f"'{existing}' is already on the list."
        if len(items) >= _MAX_SHOPPING_ITEMS:
            return (f"The list already holds {_MAX_SHOPPING_ITEMS} "
                    "items — clear some off before adding more.")
        items.append(clean)
        try:
            _save_shopping(items)
        except Exception as e:  # noqa: BLE001
            return f"Couldn't save the shopping list: {e}"
        return (f"Added '{clean}' — the list now has "
                f"{len(items)} item"
                f"{'s' if len(items) != 1 else ''}.")
    # action == "remove"
    for index, existing in enumerate(items):
        if existing.lower() == clean.lower():
            del items[index]
            try:
                _save_shopping(items)
            except Exception as e:  # noqa: BLE001
                return f"Couldn't save the shopping list: {e}"
            return (f"Removed '{existing}' — {len(items)} item"
                    f"{'s' if len(items) != 1 else ''} left.")
    if items:
        return (f"'{clean}' isn't on the list. On it now: "
                + ", ".join(items) + ".")
    return f"'{clean}' isn't on the list — the list is empty."

# ---------------------------------------------------------------------------
# PACKING LIST — weather-aware, sized by trip length
# ---------------------------------------------------------------------------

def packing_list(destination: str, days: int = 3) -> str:
    """A packing list for a trip, tuned to the live weather."""
    place = str(destination or "").strip()
    if not place:
        return ("Tell me the destination — e.g. packing_list "
                "with destination 'Goa' and days 4.")
    n = _coerce_int(days, 3, 1, 30)
    wx = _weather_now(place)
    tops = min(n + 1, 12)
    bottoms = max(2, min(n // 2 + 1, 6))
    lines = [f"Packing for {place} — {n} day"
             f"{'s' if n != 1 else ''}:"]
    if wx:
        lines.append(
            f"Right now there it's {wx['temp_c']:g}°C and "
            f"{wx['condition'].lower()} (wind "
            f"{wx['wind_kmh']:g} km/h).")
    else:
        lines.append(
            "I couldn't fetch the live weather there, so this "
            "is a general list — check the forecast before you "
            "zip up.")
    clothes = (f"• Clothes: {tops} top"
               f"{'s' if tops != 1 else ''}, {bottoms} bottom"
               f"{'s' if bottoms != 1 else ''}, {n + 1} sets of "
               "underwear and socks, sleepwear")
    if wx:
        if wx["temp_c"] <= 10:
            clothes += (", a warm jacket, a thermal layer and "
                        "a woolly hat")
        elif wx["temp_c"] <= 20:
            clothes += " and a light jacket for the evenings"
        elif wx["temp_c"] >= 27:
            clothes += (", light breathable fabrics, a sun hat "
                        "and sunscreen")
        if wx["code"] in _RAIN_CODES:
            clothes += (", an umbrella or rain jacket and shoes "
                        "that can get wet")
        if wx["code"] in _SNOW_CODES:
            clothes += ", snow boots and warm gloves"
    lines.append(clothes + ".")
    lines.append("• Toiletries: toothbrush and toothpaste, "
                 "deodorant, skincare, any medication you take "
                 "daily, and a small towel.")
    lines.append("• Tech: phone charger, power bank, earphones"
                 + (", and a plug adapter if the sockets "
                    "differ." if wx else "."))
    lines.append("• Documents and money: photo ID, bank card, "
                 "some cash, and your booking confirmations "
                 "(offline copies).")
    if n >= 5:
        lines.append("• Longer trip: a laundry bag and a spare "
                     "set of clothes in your hand luggage.")
    return "\n".join(lines)

def birthday_radar(days: int = 14) -> str:
    """Stored birthdays falling in the next `days` days
    (14 by default — the window the result words use)."""
    win = _coerce_int(days, _BIRTHDAY_WINDOW_DAYS, 1, 365)
    today = datetime.date.today()
    stored = []
    unreadable = []
    for fact in _all_facts():
        haystack = (f"{fact.get('category', '')} "
                    f"{fact.get('key', '')} "
                    f"{fact.get('value', '')}").lower()
        if "birthday" not in haystack:
            continue
        parsed = _parse_birthday(
            f"{fact.get('value', '')} {fact.get('key', '')}")
        if parsed is None:
            unreadable.append(str(fact.get("key") or "?"))
            continue
        month, day = parsed
        try:
            occurs = datetime.date(today.year, month, day)
        except ValueError:  # 29 Feb on a non-leap year — use 28th
            occurs = datetime.date(today.year, month, 28)
        if occurs < today:
            try:
                occurs = datetime.date(today.year + 1, month, day)
            except ValueError:
                occurs = datetime.date(today.year + 1, month, 28)
        stored.append((_birthday_name(fact), occurs))
    if not stored and not unreadable:
        return ("I don't have any birthdays stored yet. Tell "
                "me one — 'remember that Mum's birthday is "
                "5 March' — and I'll watch for it from then on.")
    upcoming = sorted(
        (name, occurs) for name, occurs in stored
        if 0 <= (occurs - today).days <= win)
    lines = []
    if upcoming:
        lines.append(f"Birthdays in the next "
                     f"{win} days:")
        for name, occurs in upcoming:
            away = (occurs - today).days
            if away == 0:
                when = "today!"
            elif away == 1:
                when = "tomorrow"
            else:
                when = f"in {away} days"
            lines.append(f"• {name} — {occurs:%a} {occurs.day} "
                         f"{occurs:%b} ({when})")
    elif stored:
        name, occurs = min(stored, key=lambda pair: pair[1])
        away = (occurs - today).days
        lines.append(
            f"No birthdays in the next {win} "
            f"days. The next one I know is {name} — "
            f"{occurs:%a} {occurs.day} {occurs:%b} (in {away} "
            "days).")
    if unreadable:
        lines.append(
            f"{len(unreadable)} stored birthday note"
            f"{'s have dates' if len(unreadable) != 1 else ' has a date'} "
            "I can't read: " + ", ".join(
                f"'{k}'" for k in unreadable) + ".")
    return "\n".join(lines)
