"""Part of the skills_money split (Phase A batch A14): see
skills_money.py (facade) for the pack docstring and registration.
Move only — bodies verbatim from the original module.
"""


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


def _load_subscriptions() -> tuple[list, int]:
    """(entries, broken_count): {id, key, monthly} dicts parsed
    from the subscriptions facts; unparseable ones counted."""
    entries = []
    broken = 0
    for fact in _facts(_SUBSCRIPTIONS_CATEGORY):
        try:
            monthly = float(fact.get("value") or "")
            entries.append({
                "id": fact.get("id"),
                "key": str(fact.get("key") or ""),
                "monthly": monthly,
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
                       monthly=0, currency: str = "") -> str:
    """Track subscriptions: 'add' records (or updates) one at
    its monthly cost, 'remove' drops one, 'list' shows each
    with its yearly cost, the totals, and the biggest one."""
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
        lines = [f"Subscriptions ({len(entries)}):"]
        for entry in entries:
            lines.append(
                f"• {entry['key']} — "
                f"{_money(entry['monthly'], cur)} a month "
                f"({_money(entry['monthly'] * 12, cur)} a year)")
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
        try:
            _store().save_fact(
                _SUBSCRIPTIONS_CATEGORY,
                existing["key"] if existing else clean,
                _plain_num(value))
        except Exception as e:  # noqa: BLE001
            return f"Couldn't save the subscription: {e}"
        verb = "Updated" if existing else "Added"
        return (f"{verb} '{clean}' at "
                f"{_money(value, cur)} a month — "
                f"{_money(value * 12, cur)} a year.")
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
