"""Social track (v5.73) — SO-2 occasions/promises/gifts/debts +
SO-2.6 respectful nudges, on a PARALLEL store (INV-8).

This module does NOT write to the Wave 6 vault and does NOT
mutate its schema. Vault promises (people.write_promise:
person + text + due) stay the simple form; this ledger is the
rich layer (Taskwarrior attributes, re-implemented, not code):
counterparty, due, wait, until, depends, recur template+child,
annotations, and an explained, inspectable urgency score.

Never-inferred rules, enforced by construction:
- Entries exist only from values HE states or confirms. No
  function here reads mail, messages, calendars, or chats to
  create an entry, and no function infers that a debt exists,
  that someone is neglected, or that a contact is important.
- A nudge fires only on a cadence HE set, for a person HE named,
  once, with its reason shown. Snooze/dismiss/pause-per-person
  carry no penalty, streak loss, or score decay. Quiet hours
  and pauses are honoured from HIS tap, never detected.
- A nudge never drafts-and-sends and never auto-messages. It
  suggests an action he chose; he sends.

Store: one JSON file (default data/social_ledger_v573.json).
All functions take store_path explicitly in tests; the default
is only a convenience for library callers.
"""

import json
import os
from datetime import date, timedelta

from .social_occasion_dates import make_occasion_date, next_occurrence

DEFAULT_STORE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "social_ledger_v573.json")

# Urgency coefficients, documented and inspectable (Taskwarrior
# semantics, re-tuned for social items: a birthday outranks a
# generic promise; waiting and blocked items sink, never rise).
COEF_OVERDUE_PER_DAY = 2.0
COEF_DUE_TODAY = 12.0
COEF_DUE_SOON_PER_DAY = 1.0  # within 7 days, decaying
COEF_WAITING = -3.0
COEF_BLOCKED = -5.0
COEF_BIRTHDAY = 5.0


def default_store_path():
    return os.path.normpath(DEFAULT_STORE)


def _load(path):
    if not os.path.exists(path):
        return {"occasions": [], "promises": [], "gifts": [],
                "debts": [], "nudges": []}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    for key in ("occasions", "promises", "gifts", "debts", "nudges"):
        data.setdefault(key, [])
    return data


def _save(path, data):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _next_id(data, kind):
    return f"{kind}-{len(data[kind]) + 1}"


def _parse_day(text):
    try:
        year, month, day = (int(p) for p in str(text).split("-"))
        return date(year, month, day)
    except (ValueError, AttributeError):
        raise ValueError(f"expected YYYY-MM-DD, got {text!r}")


def _today(text=None):
    return _parse_day(text) if text else date.today()


# --- Occasions (SO-2.1 data model, SO-2.5 date rules) ---

def add_occasion(store_path, person, occ_type, month, day, year=None,
                 leap_policy="feb28", lead_times=(), note=""):
    """Add an occasion. occ_type: birthday | anniversary | custom.
    Date parts are exactly what he supplied (year may be unknown).
    lead_times: days-before to surface (e.g. [30, 7, 0])."""
    if occ_type not in ("birthday", "anniversary", "custom"):
        raise ValueError(f"occ_type must be birthday/anniversary/"
                         f"custom, got {occ_type!r}")
    if not str(person or "").strip():
        raise ValueError("occasion requires a person")
    occ_date = make_occasion_date(month, day, year=year,
                                  leap_policy=leap_policy)
    data = _load(store_path)
    entry = {"id": _next_id(data, "occasions"), "person": person,
             "type": occ_type, "date": occ_date,
             "lead_times": sorted(int(t) for t in lead_times),
             "note": note or ""}
    data["occasions"].append(entry)
    _save(store_path, data)
    return entry


def upcoming_occasions(store_path, today, horizon_days=60):
    """Occasions whose next occurrence falls within horizon_days
    of today, each with its next date and the lead-time that
    fired. Sorted by next date. A birthday outranks a generic
    promise only in the briefing order, never as a score about
    the person."""
    today_d = _today(today)
    data = _load(store_path)
    out = []
    for entry in data["occasions"]:
        nxt = next_occurrence(entry["date"], today_d.isoformat())
        nxt_d = date(*nxt)
        delta = (nxt_d - today_d).days
        fired = [t for t in entry["lead_times"] if delta <= t]
        if 0 <= delta <= horizon_days:
            out.append({"occasion": entry, "next": nxt_d.isoformat(),
                        "days_away": delta,
                        "lead_fired": bool(fired)})
    out.sort(key=lambda item: (item["days_away"],
                               0 if item["occasion"]["type"] == "birthday"
                               else 1))
    return out


# --- Stay-in-touch + nudges (SO-2.6) ---

def set_stay_in_touch(store_path, person, cadence_days,
                      last_contact_logged=""):
    """Cadence HE set, for a person HE named. last_contact_logged
    is a date HE logged, or '' when none."""
    if int(cadence_days) <= 0:
        raise ValueError("cadence_days must be positive")
    if last_contact_logged:
        _parse_day(last_contact_logged)
    data = _load(store_path)
    entry = {"person": person, "cadence_days": int(cadence_days),
             "last_contact_logged": last_contact_logged or "",
             "snoozed_until": "", "paused": False}
    data["nudges"] = [n for n in data["nudges"]
                      if n["person"] != person]
    data["nudges"].append(entry)
    _save(store_path, data)
    return entry


def snooze_person(store_path, person, days, today):
    data = _load(store_path)
    for entry in data["nudges"]:
        if entry["person"] == person:
            entry["snoozed_until"] = (
                _today(today) + timedelta(days=int(days))).isoformat()
            _save(store_path, data)
            return entry
    raise ValueError(f"no stay-in-touch record for {person!r}")


def pause_person(store_path, person, paused=True):
    data = _load(store_path)
    for entry in data["nudges"]:
        if entry["person"] == person:
            entry["paused"] = bool(paused)
            _save(store_path, data)
            return entry
    raise ValueError(f"no stay-in-touch record for {person!r}")


def nudge_decision(store_path, person, today, quiet_hours=False):
    """The SO-2.6 gate. Returns {fire, reason}. Fires only when:
    due (today >= last_contact + cadence), not paused, not
    snoozed, not quiet hours. The reason always states the
    user-set cadence and his logged contact — if it cannot state
    its reason in one sentence, it does not fire."""
    data = _load(store_path)
    entry = next((n for n in data["nudges"] if n["person"] == person),
                 None)
    if entry is None:
        return {"fire": False,
                "reason": f"no stay-in-touch cadence set for {person}"}
    today_d = _today(today)
    if entry["paused"]:
        return {"fire": False,
                "reason": f"paused for {person} (his tap, no penalty)"}
    if entry["snoozed_until"] and today_d.isoformat() <= entry["snoozed_until"]:
        return {"fire": False,
                "reason": f"snoozed for {person} until "
                          f"{entry['snoozed_until']}"}
    if quiet_hours:
        return {"fire": False, "reason": "quiet hours honoured"}
    last = entry["last_contact_logged"]
    if not last:
        return {"fire": False,
                "reason": f"no contact ever logged by him for {person}"}
    due = _parse_day(last) + timedelta(days=entry["cadence_days"])
    if today_d < due:
        return {"fire": False,
                "reason": f"not due for {person} until {due.isoformat()}"}
    return {"fire": True,
            "reason": f"You asked: every {entry['cadence_days']} days. "
                      f"Last call logged by you: {last}."}


# --- Promises (SO-2.2 attributes) ---

def add_promise(store_path, counterparty, text, due="", wait="",
                until="", depends=(), recur_days=None,
                party="me"):
    """A promise is not a to-do: counterparty, due, wait (hidden
    until), until (expires/auto-drops), depends (blocked-by),
    recur (template+child), party me|them|both (SO-2.3's steal:
    whose side the obligation is on)."""
    if not str(counterparty or "").strip() or not str(text or "").strip():
        raise ValueError("promise requires a counterparty and text")
    if party not in ("me", "them", "both"):
        raise ValueError(f"party must be me/them/both, got {party!r}")
    for field in (due, wait, until):
        if field:
            _parse_day(field)
    data = _load(store_path)
    entry = {"id": _next_id(data, "promises"),
             "counterparty": counterparty, "text": text,
             "due": due or "", "wait": wait or "",
             "until": until or "", "depends": list(depends),
             "recur_days": recur_days, "party": party,
             "status": "open", "annotations": []}
    data["promises"].append(entry)
    _save(store_path, data)
    return entry


def annotate_promise(store_path, promise_id, text, today):
    data = _load(store_path)
    for entry in data["promises"]:
        if entry["id"] == promise_id:
            entry["annotations"].append(
                {"date": _today(today).isoformat(), "text": text})
            _save(store_path, data)
            return entry
    raise ValueError(f"no promise {promise_id!r}")


def promise_urgency(store_path, promise_id, today):
    """Explained, inspectable urgency: (score, [reasons]). Never
    a black-box priority, never raised because the counterparty
    is 'important' — importance is never an input."""
    data = _load(store_path)
    entry = next((p for p in data["promises"]
                  if p["id"] == promise_id), None)
    if entry is None:
        raise ValueError(f"no promise {promise_id!r}")
    today_d = _today(today)
    score, reasons = 0.0, []
    if entry["status"] != "open":
        return (0.0, [f"status is {entry['status']}"])
    if entry["wait"] and today_d < _parse_day(entry["wait"]):
        return (COEF_WAITING,
                [f"waiting until {entry['wait']} (hidden until then)"])
    blockers = [d for d in entry["depends"]
                if any(p["id"] == d and p["status"] == "open"
                       for p in data["promises"])]
    if blockers:
        score += COEF_BLOCKED
        reasons.append(f"blocked by open promise(s): {blockers}")
    if entry["due"]:
        due_d = _parse_day(entry["due"])
        delta = (due_d - today_d).days
        if delta < 0:
            score += COEF_DUE_TODAY + COEF_OVERDUE_PER_DAY * (-delta)
            reasons.append(f"overdue by {-delta} day(s)")
        elif delta == 0:
            score += COEF_DUE_TODAY
            reasons.append("due today")
        elif delta <= 7:
            score += COEF_DUE_SOON_PER_DAY * (7 - delta)
            reasons.append(f"due in {delta} day(s)")
    if not reasons:
        reasons.append("open, no due pressure")
    return (score, reasons)


def visible_promises(store_path, today):
    """Promises visible today: wait-future hidden, until-past
    excluded (sweep_expired drops them). History is never
    mutated by reading."""
    today_d = _today(today)
    data = _load(store_path)
    out = []
    for entry in data["promises"]:
        if entry["status"] != "open":
            continue
        if entry["wait"] and today_d < _parse_day(entry["wait"]):
            continue
        if entry["until"] and today_d > _parse_day(entry["until"]):
            continue
        out.append(entry)
    return out


def sweep_expired_promises(store_path, today):
    """Until-past open promises auto-drop (status=dropped).
    Returns dropped ids. Completing history is never rewritten."""
    today_d = _today(today)
    data = _load(store_path)
    dropped = []
    for entry in data["promises"]:
        if entry["status"] == "open" and entry["until"] \
                and today_d > _parse_day(entry["until"]):
            entry["status"] = "dropped"
            dropped.append(entry["id"])
    if dropped:
        _save(store_path, data)
    return dropped


def complete_promise(store_path, promise_id, today):
    """Complete a promise. Recurring promises spawn the NEXT
    child instance (due/wait shifted by recur_days); the
    completed instance's history is never mutated."""
    data = _load(store_path)
    entry = next((p for p in data["promises"]
                  if p["id"] == promise_id), None)
    if entry is None:
        raise ValueError(f"no promise {promise_id!r}")
    entry["status"] = "done"
    child = None
    if entry["recur_days"]:
        shift = timedelta(days=int(entry["recur_days"]))
        child = dict(entry)
        child["id"] = _next_id(data, "promises")
        child["status"] = "open"
        child["annotations"] = []
        if entry["due"]:
            child["due"] = (_parse_day(entry["due"]) + shift).isoformat()
        if entry["wait"]:
            child["wait"] = (_parse_day(entry["wait"]) + shift).isoformat()
        data["promises"].append(child)
    _save(store_path, data)
    return {"completed": entry, "child": child}


def import_vault_promise(record):
    """Bridge FROM a Wave 6 vault promise record (people
    shape: key/value/due_date) into a ledger promise dict.
    Read-only from the vault: this function writes nothing to
    the vault and never re-interprets the vault record."""
    due = str(record.get("due_date", "") or "")
    text = str(record.get("value", "") or "")
    if text.startswith("Promise to "):
        rest = text[len("Promise to "):]
        counterparty, _, promise_text = rest.partition(": ")
        if not promise_text:
            counterparty, promise_text = rest, rest
    else:
        counterparty, promise_text = "", text
    return {"counterparty": counterparty, "text": promise_text,
            "due": due, "wait": "", "until": "", "depends": [],
            "recur_days": None, "party": "me", "status": "open",
            "annotations": []}


# --- Gifts & debts (SO-2.1 ledgers he keeps) ---

def add_gift(store_path, person, kind, description, date_text="",
             occasion=""):
    if kind not in ("idea", "given", "received"):
        raise ValueError(f"gift kind must be idea/given/received, "
                         f"got {kind!r}")
    if date_text:
        _parse_day(date_text)
    data = _load(store_path)
    entry = {"id": _next_id(data, "gifts"), "person": person,
             "kind": kind, "description": description,
             "date": date_text or "", "occasion": occasion}
    data["gifts"].append(entry)
    _save(store_path, data)
    return entry


def add_debt(store_path, counterparty, amount_minor, currency,
             direction, loaned_at=""):
    """amount_minor: integer minor units (paise/cents) — never
    floats for money. direction: 'i_owe' | 'they_owe'."""
    if not isinstance(amount_minor, int) or amount_minor < 0:
        raise ValueError("amount_minor must be a non-negative int")
    if direction not in ("i_owe", "they_owe"):
        raise ValueError(f"direction must be i_owe/they_owe, "
                         f"got {direction!r}")
    if loaned_at:
        _parse_day(loaned_at)
    data = _load(store_path)
    entry = {"id": _next_id(data, "debts"),
             "counterparty": counterparty,
             "amount_minor": amount_minor, "currency": currency,
             "direction": direction, "settled": False,
             "loaned_at": loaned_at or "", "settled_at": ""}
    data["debts"].append(entry)
    _save(store_path, data)
    return entry


def settle_debt(store_path, debt_id, today):
    data = _load(store_path)
    for entry in data["debts"]:
        if entry["id"] == debt_id:
            entry["settled"] = True
            entry["settled_at"] = _today(today).isoformat()
            _save(store_path, data)
            return entry
    raise ValueError(f"no debt {debt_id!r}")


def open_balances(store_path):
    """Net open balance per counterparty per currency, in minor
    units. Positive = they owe him; negative = he owes them.
    A ledger he keeps — never a balance inferred from chats or
    payments."""
    data = _load(store_path)
    balances = {}
    for entry in data["debts"]:
        if entry["settled"]:
            continue
        key = (entry["counterparty"], entry["currency"])
        sign = 1 if entry["direction"] == "they_owe" else -1
        balances[key] = balances.get(key, 0) + sign * entry["amount_minor"]
    return [{"counterparty": c, "currency": cur, "net_minor": net}
            for (c, cur), net in sorted(balances.items())]
