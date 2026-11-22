"""Part of the skills_money split (Phase A batch A14): see
skills_money.py (facade) for the pack docstring and registration.
Move only — bodies verbatim from the original module.
"""


import calendar
import datetime
import json


# INTENTIONAL: cross-part names — budget functions resolve these bare
# names in their defining module's globals, so they are imported here,
# not relied on from the facade (bare-name rule, plan §2 P3).
from .skills_money_helpers import (
    _coerce_float,
    _facts,
    _money,
    _plain_num,
    _store,
)


# ---------------------------------------------------------------------------
# BUDGET ENVELOPE — named monthly budgets as memory facts
# ---------------------------------------------------------------------------

_ENVELOPES_CATEGORY = "envelopes"


def _load_envelopes() -> tuple[list, int]:
    """(entries, broken_count): {key, budget, spent} dicts
    parsed from the envelopes facts; unparseable ones counted."""
    entries = []
    broken = 0
    for fact in _facts(_ENVELOPES_CATEGORY):
        try:
            stored = json.loads(fact.get("value") or "")
            entries.append({
                "key": str(fact.get("key") or ""),
                "budget": float(stored["budget"]),
                "spent": float(stored["spent"]),
            })
        except Exception:  # noqa: BLE001
            broken += 1
    entries.sort(key=lambda e: e["key"].lower())
    return entries, broken


def _find_envelope(entries: list, name: str):
    wanted = name.strip().lower()
    for entry in entries:
        if entry["key"].strip().lower() == wanted:
            return entry
    return None


def _envelope_line(entry: dict, cur: str) -> str:
    budget, spent = entry["budget"], entry["spent"]
    pct = (spent / budget * 100.0) if budget > 0 else 0.0
    if spent <= budget:
        tail = f"{_money(budget - spent, cur)} left"
    else:
        tail = f"{_money(spent - budget, cur)} OVER budget"
    return (f"• {entry['key']} — budget "
            f"{_money(budget, cur)}, spent "
            f"{_money(spent, cur)}, {tail} ({pct:.0f}% used)")


def budget_envelope(action: str = "", name: str = "", amount=0,
                    currency: str = "") -> str:
    """Budget envelopes: 'set' creates or resets one with a
    budget, 'spend' adds spending to one, 'report' shows every
    envelope's budget, spent, what's left and % used."""
    action = str(action or "").strip().lower()
    if action not in ("set", "spend", "report"):
        return ("Envelope actions are 'set', 'spend' and "
                "'report' — e.g. budget_envelope with action "
                "'set', a name like 'Groceries' and the budget "
                "as the amount.")
    cur = str(currency or "")
    try:
        entries, broken = _load_envelopes()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the envelopes: {e}"
    broken_note = ""
    if broken:
        broken_note = (f" ({broken} stored envelope"
                       f"{'s' if broken != 1 else ''} couldn't "
                       "be read and were skipped.)")
    if action == "report":
        if not entries:
            return ("No envelopes yet. Create one with "
                    "budget_envelope, action 'set', a name and "
                    f"a budget amount.{broken_note}")
        lines = [f"Budget envelopes ({len(entries)}):"]
        lines += [_envelope_line(e, cur) for e in entries]
        lines.append(
            f"Totals: budgets "
            f"{_money(sum(e['budget'] for e in entries), cur)}, "
            f"spent "
            f"{_money(sum(e['spent'] for e in entries), cur)}.")
        if broken:
            lines.append(broken_note.strip())
        return "\n".join(lines)
    clean = " ".join(str(name or "").split())
    if not clean:
        return "That needs the envelope's name."
    value = _coerce_float(amount)
    if value is None or value <= 0:
        return (f"The amount for '{action}' needs to be a "
                "number more than zero.")
    existing = _find_envelope(entries, clean)
    if action == "set":
        try:
            _store().save_fact(
                _ENVELOPES_CATEGORY,
                existing["key"] if existing else clean,
                json.dumps({"budget": value, "spent": 0.0}))
        except Exception as e:  # noqa: BLE001
            return f"Couldn't save the envelope: {e}"
        verb = "Reset" if existing else "Set"
        return (f"{verb} the '{clean}' envelope: budget "
                f"{_money(value, cur)}, spent back to "
                f"{_money(0, cur)}.")
    # action == "spend"
    if existing is None:
        known = (", ".join(e["key"] for e in entries)
                 if entries else "")
        extra = (f" Your envelopes: {known}." if known else
                 " You have no envelopes yet — create one with "
                 "action 'set' first.")
        return (f"There's no envelope called '{clean}'."
                f"{extra}")
    new_spent = existing["spent"] + value
    try:
        _store().save_fact(
            _ENVELOPES_CATEGORY, existing["key"],
            json.dumps({"budget": existing["budget"],
                        "spent": new_spent}))
    except Exception as e:  # noqa: BLE001
        return f"Couldn't update the envelope: {e}"
    budget = existing["budget"]
    pct = (new_spent / budget * 100.0) if budget > 0 else 0.0
    result = (f"Added {_money(value, cur)} to "
              f"'{existing['key']}'. Spent "
              f"{_money(new_spent, cur)} of "
              f"{_money(budget, cur)} ({pct:.0f}% used)")
    if new_spent <= budget:
        result += (f" — {_money(budget - new_spent, cur)} "
                   "left.")
    else:
        result += (f" — that's "
                   f"{_money(new_spent - budget, cur)} OVER "
                   "the budget.")
    return result


# ---------------------------------------------------------------------------
# SUBSCRIPTION AUDIT — what the month quietly costs, as memory facts
# ---------------------------------------------------------------------------

_SUBSCRIPTIONS_CATEGORY = "subscriptions"


def _next_renewal(day: int, today: datetime.date):
    """The next date with day-of-month `day`, clamped to the
    month's last day (a 31st renews on 28 Feb). None when `day`
    is not a day of the month (1–31)."""
    try:
        day = int(day)
    except (TypeError, ValueError):
        return None
    if day < 1 or day > 31:
        return None
    year, month = today.year, today.month
    last = calendar.monthrange(year, month)[1]
    candidate = datetime.date(year, month, min(day, last))
    if candidate >= today:
        return candidate
    if month == 12:
        year, month = year + 1, 1
    else:
        month += 1
    last = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, min(day, last))


def _load_subscriptions() -> tuple[list, int]:
    """(entries, broken_count): {id, key, monthly, renewal_day}
    dicts parsed from the subscriptions facts; unparseable ones
    counted. Values are JSON {"monthly": X, "renewal_day": D}
    since Stage 0 features batch 3 (Group F) — legacy plain
    numbers (monthly cost only) still read, renewal_day None."""
    entries = []
    broken = 0
    for fact in _facts(_SUBSCRIPTIONS_CATEGORY):
        try:
            raw = fact.get("value") or ""
            try:
                stored = json.loads(raw)
            except (TypeError, ValueError):
                stored = None
            if isinstance(stored, dict) and "monthly" in stored:
                monthly = float(stored["monthly"])
                renewal_day = stored.get("renewal_day")
                renewal_day = (int(renewal_day)
                               if renewal_day is not None else None)
                if renewal_day is not None and not 1 <= renewal_day <= 31:
                    renewal_day = None
            else:
                monthly = float(raw)
                renewal_day = None
            entries.append({
                "id": fact.get("id"),
                "key": str(fact.get("key") or ""),
                "monthly": monthly,
                "renewal_day": renewal_day,
            })
        except Exception:  # noqa: BLE001
            broken += 1
    entries.sort(key=lambda e: -e["monthly"])
    return entries, broken


def _find_subscription(entries: list, name: str):
    wanted = name.strip().lower()
    for entry in entries:
        if entry["key"].strip().lower() == wanted:
            return entry
    return None


def subscription_audit(action: str = "", name: str = "",
                       monthly=0, currency: str = "",
                       renewal_day=0) -> str:
    """Track subscriptions: 'add' records (or updates) one at
    its monthly cost, 'remove' drops one, 'list' shows each
    with its yearly cost, the totals, and the biggest one.
    'add' takes an optional renewal_day (1–31, the day of the
    month it renews); 'list' shows the next renewal date for
    any subscription that has one."""
    action = str(action or "").strip().lower()
    if action not in ("add", "remove", "list"):
        return ("Subscription actions are 'add', 'remove' and "
                "'list' — e.g. subscription_audit with action "
                "'add', a name and its monthly cost.")
    cur = str(currency or "")
    try:
        entries, broken = _load_subscriptions()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the subscriptions: {e}"
    broken_note = ""
    if broken:
        broken_note = (f" ({broken} stored subscription"
                       f"{'s' if broken != 1 else ''} couldn't "
                       "be read and were skipped.)")
    if action == "list":
        if not entries:
            return ("No subscriptions recorded yet. Add one "
                    "with subscription_audit, action 'add', a "
                    f"name and its monthly cost.{broken_note}")
        today = datetime.date.today()
        lines = [f"Subscriptions ({len(entries)}):"]
        for entry in entries:
            line = (f"• {entry['key']} — "
                    f"{_money(entry['monthly'], cur)} a month "
                    f"({_money(entry['monthly'] * 12, cur)} a year)")
            if entry.get("renewal_day"):
                nxt = _next_renewal(entry["renewal_day"], today)
                if nxt is not None:
                    line += f", renews {nxt.isoformat()}"
            lines.append(line)
        total_month = sum(e["monthly"] for e in entries)
        lines.append(f"Total: {_money(total_month, cur)} a "
                     f"month, {_money(total_month * 12, cur)} "
                     "a year.")
        biggest = entries[0]
        lines.append(f"The biggest is '{biggest['key']}' at "
                     f"{_money(biggest['monthly'], cur)} a "
                     "month.")
        if broken:
            lines.append(broken_note.strip())
        return "\n".join(lines)
    clean = " ".join(str(name or "").split())
    if not clean:
        return "That needs the subscription's name."
    existing = _find_subscription(entries, clean)
    if action == "add":
        value = _coerce_float(monthly)
        if value is None or value <= 0:
            return ("Adding a subscription needs its monthly "
                    "cost, a number more than zero.")
        day = None
        if renewal_day:
            try:
                day = int(renewal_day)
            except (TypeError, ValueError):
                day = None
            if day is not None and not 1 <= day <= 31:
                return ("The renewal day has to be a day of the "
                        "month, 1 to 31.")
        if day is None and existing is not None:
            day = existing.get("renewal_day")
        stored = (json.dumps({"monthly": value,
                              "renewal_day": day})
                  if day is not None else _plain_num(value))
        try:
            _store().save_fact(
                _SUBSCRIPTIONS_CATEGORY,
                existing["key"] if existing else clean,
                stored)
        except Exception as e:  # noqa: BLE001
            return f"Couldn't save the subscription: {e}"
        verb = "Updated" if existing else "Added"
        result = (f"{verb} '{clean}' at "
                  f"{_money(value, cur)} a month — "
                  f"{_money(value * 12, cur)} a year.")
        if day is not None:
            nxt = _next_renewal(day, datetime.date.today())
            if nxt is not None:
                result += f" Renews {nxt.isoformat()}."
        return result
    # action == "remove"
    if existing is None:
        known = (", ".join(e["key"] for e in entries)
                 if entries else "")
        extra = (f" Recorded: {known}." if known else
                 " None are recorded.")
        return (f"There's no subscription called '{clean}'."
                f"{extra}")
    try:
        _store().delete_fact(existing["id"])
    except Exception as e:  # noqa: BLE001
        return f"Couldn't remove the subscription: {e}"
    rest = sum(e["monthly"] for e in entries
               if e is not existing)
    result = (f"Removed '{existing['key']}' "
              f"({_money(existing['monthly'], cur)} a month).")
    if entries and len(entries) > 1:
        result += (f" The rest still total "
                   f"{_money(rest, cur)} a month.")
    else:
        result += " No subscriptions left."
    return result


# ---------------------------------------------------------------------------
# PORTFOLIO TRACKER — holdings + total value (Stage 0 features
# batch 3, Group F, 2026-11-22)
#
# Holdings are the user's own records, as memory facts (the
# envelopes precedent): one fact per symbol, JSON value
# {"quantity": N, "buy_price": X|null, "price": Y|null}.
#
# Live valuation is HONESTLY UNAVAILABLE: both live price tools
# are gated and answer with refusals, making no network call —
# get_stock_quote (Yahoo's terms forbid commercial display; no
# licensed source is configured) and get_crypto_price
# (CoinGecko's keyless tier is not for commercial display). A
# licensed source is an owner purchase decision, not a build
# step. So a holding is valued ONLY from a price the user
# records: buy_price is the cost basis per unit, price is the
# user's own recorded current price per unit. A holding with no
# recorded price is listed as unvalued with the plain line that
# live quotes are off — never a fabricated price, never a
# silent zero. No advice, no trading, no predictions.
# ---------------------------------------------------------------------------

_PORTFOLIO_CATEGORY = "portfolio"


def _load_portfolio() -> tuple[list, int]:
    """(entries, broken_count): {id, key, quantity, buy_price,
    price} dicts parsed from the portfolio facts; unparseable
    ones counted. Keys are upper-cased symbols."""
    entries = []
    broken = 0
    for fact in _facts(_PORTFOLIO_CATEGORY):
        try:
            stored = json.loads(fact.get("value") or "")
            if not isinstance(stored, dict):
                raise ValueError("not a holding")
            quantity = float(stored["quantity"])
            if quantity <= 0:
                raise ValueError("quantity must be positive")
            buy = stored.get("buy_price")
            buy = float(buy) if buy is not None else None
            if buy is not None and buy <= 0:
                buy = None
            current = stored.get("price")
            current = float(current) if current is not None else None
            if current is not None and current <= 0:
                current = None
            entries.append({
                "id": fact.get("id"),
                "key": str(fact.get("key") or "").strip().upper(),
                "quantity": quantity,
                "buy_price": buy,
                "price": current,
            })
        except Exception:  # noqa: BLE001
            broken += 1
    entries.sort(key=lambda e: e["key"])
    return entries, broken


def _find_holding(entries: list, symbol: str):
    wanted = (symbol or "").strip().upper()
    for entry in entries:
        if entry["key"] == wanted:
            return entry
    return None


def _holding_line(entry: dict, cur: str) -> str:
    qty = entry["quantity"]
    qty_text = f"{qty:g}"
    bits = [f"• {entry['key']} — {qty_text} held"]
    if entry.get("buy_price") is not None:
        bits.append(f"cost {_money(entry['buy_price'], cur)} each")
    if entry.get("price") is not None:
        bits.append(
            f"recorded price {_money(entry['price'], cur)} each, "
            f"value {_money(qty * entry['price'], cur)}")
    else:
        bits.append("no recorded price — live quotes are off")
    return ", ".join(bits) + "."


def portfolio_tracker(action: str = "", symbol: str = "",
                      quantity=0, buy_price=0, price=0,
                      currency: str = "") -> str:
    """Track a portfolio of holdings: 'add' records shares of a
    symbol (adding more of one you hold adds to the quantity;
    buy_price is the cost basis per unit, price is your own
    recorded current price per unit — both optional), 'remove'
    drops a holding, 'list' shows every holding with its value,
    'total' sums what you put in and what it's worth now from
    the prices you recorded. Live quotes are off — no licensed
    price source is configured yet — so a holding with no
    recorded price is unvalued, never guessed. No advice, no
    trading."""
    action = str(action or "").strip().lower()
    if action not in ("add", "remove", "list", "total"):
        return ("Portfolio actions are 'add', 'remove', 'list' "
                "and 'total' — e.g. portfolio_tracker with "
                "action 'add', a symbol like 'AAPL' and a "
                "quantity.")
    cur = str(currency or "")
    try:
        entries, broken = _load_portfolio()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the portfolio: {e}"
    broken_note = ""
    if broken:
        broken_note = (f" ({broken} stored holding"
                       f"{'s' if broken != 1 else ''} couldn't "
                       "be read and were skipped.)")
    if action in ("list", "total"):
        if not entries:
            return ("No holdings recorded yet. Add one with "
                    "portfolio_tracker, action 'add', a symbol "
                    f"and a quantity.{broken_note}")
        if action == "list":
            lines = [f"Holdings ({len(entries)}):"]
            lines += [_holding_line(e, cur) for e in entries]
            if broken:
                lines.append(broken_note.strip())
            return "\n".join(lines)
        invested = sum(e["quantity"] * e["buy_price"]
                       for e in entries
                       if e.get("buy_price") is not None)
        current = sum(e["quantity"] * e["price"]
                      for e in entries
                      if e.get("price") is not None)
        unvalued = [e["key"] for e in entries
                    if e.get("price") is None]
        lines = [f"Portfolio ({len(entries)} holding"
                 f"{'s' if len(entries) != 1 else ''}):"]
        if any(e.get("buy_price") is not None for e in entries):
            lines.append(f"Invested: {_money(invested, cur)} "
                         "(from the cost prices you recorded).")
        if any(e.get("price") is not None for e in entries):
            lines.append(f"Current value: {_money(current, cur)} "
                         "(from the prices you recorded).")
        if unvalued:
            lines.append(
                f"Unvalued: {', '.join(unvalued)} — no recorded "
                "price, and live quotes are off (no licensed "
                "price source is configured yet), so I can't "
                "value them.")
        if broken:
            lines.append(broken_note.strip())
        return "\n".join(lines)
    clean = " ".join(str(symbol or "").split()).upper()
    if not clean:
        return "That needs the holding's symbol, like 'AAPL'."
    existing = _find_holding(entries, clean)
    if action == "add":
        qty = _coerce_float(quantity)
        if qty is None or qty <= 0:
            return ("Adding a holding needs a quantity more "
                    "than zero.")
        buy = _coerce_float(buy_price)
        if buy is not None and buy <= 0:
            buy = None
        current = _coerce_float(price)
        if current is not None and current <= 0:
            current = None
        new_qty = qty + (existing["quantity"] if existing else 0.0)
        new_buy = buy if buy is not None else (
            existing["buy_price"] if existing else None)
        new_price = current if current is not None else (
            existing["price"] if existing else None)
        try:
            _store().save_fact(
                _PORTFOLIO_CATEGORY, clean,
                json.dumps({"quantity": new_qty,
                            "buy_price": new_buy,
                            "price": new_price}))
        except Exception as e:  # noqa: BLE001
            return f"Couldn't save the holding: {e}"
        # A1-store: re-read before claiming the add.
        try:
            after, _b = _load_portfolio()
            saved = _find_holding(after, clean)
        except Exception:  # noqa: BLE001
            saved = None
        if saved is None or saved["quantity"] != new_qty:
            return (f"I tried to record {clean} but couldn't "
                    "confirm it stuck — nothing was claimed.")
        verb = "Added to" if existing else "Added"
        result = (f"{verb} '{clean}': {new_qty:g} held")
        if new_buy is not None:
            result += f", cost {_money(new_buy, cur)} each"
        if new_price is not None:
            result += (f", recorded price "
                       f"{_money(new_price, cur)} each, value "
                       f"{_money(new_qty * new_price, cur)}")
        return result + "."
    # action == "remove"
    if existing is None:
        known = (", ".join(e["key"] for e in entries)
                 if entries else "")
        extra = (f" Held: {known}." if known else
                 " Nothing is held.")
        return (f"There's no holding called '{clean}'."
                f"{extra}")
    try:
        _store().delete_fact(existing["id"])
    except Exception as e:  # noqa: BLE001
        return f"Couldn't remove the holding: {e}"
    try:
        after, _b = _load_portfolio()
        gone = _find_holding(after, clean) is None
    except Exception:  # noqa: BLE001
        gone = False
    if not gone:
        return (f"I tried to remove '{clean}' but it's still "
                "there — nothing was claimed.")
    result = f"Removed '{clean}' ({existing['quantity']:g} held)."
    if entries and len(entries) > 1:
        result += f" {len(entries) - 1} holding(s) left."
    else:
        result += " No holdings left."
    return result
