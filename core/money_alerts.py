"""Money alerts — the proactive half of the money tools
(Stage 0 features batch 3, Group F, 2026-11-22).

Three alert kinds, one check:

- over-budget envelopes (spent > budget);
- price watches whose fetched price is at or under target;
- subscriptions whose next renewal falls within 7 days.

Two surfaces, one evaluation, deliberately different semantics:

- `check_money_alerts` (the tool) reports NEWLY-DUE alerts only.
  It keeps a dedupe ledger (`data/money_alerts.json`) so each
  alert fires once until its numbers change — a standing condition
  does not re-fire on every check. The price leg fetches each
  watched page (the `price_watch` check shape); the other two legs
  read the owner's own stores.
- `money_alert_lines_for_briefing` (the morning-briefing section)
  reports STANDING alerts from last-known prices — no fetch, no
  ledger write — so the briefing shows what needs attention each
  morning until it is resolved.

Cadence, stated plainly: checks run when the user asks, when a
scheduled routine runs them (RoutineScheduler, 20 s tick, a
user-scheduled slot — e.g. every morning at 08:00), and the
morning briefing shows standing money alerts each morning. No
background polling, no new daemon, no nagging: an alert fires
once per crossing, then waits for the numbers to move.

Classification: effectful, FREE by design (app-state) — the only
writes are the ledger file and the price watches' own
last_price/last_checked fields, both inside VYUK's own data
store. The read half is owner-only (L-24): the check reads the
owner's budgets, watches and subscriptions, so it is refused in
an unproven live-origin session like `subscription_audit`.
"""

from __future__ import annotations

import calendar
import datetime
import json
import os
import tempfile
from pathlib import Path

from .skills_money_budget import (
    _load_envelopes,
    _load_subscriptions,
    _next_renewal,
)
from .skills_money_helpers import _money
from .skills_web6_helpers import _PAGE_CAP, _fetch, _visible_text
from .skills_web6_watch import (
    _first_price,
    _load_prices,
    _save_prices,
)

_ALERTS_FILE = "money_alerts.json"

# A renewal counts as "coming up" inside this many days.
RENEWAL_WINDOW_DAYS = 7


def _default_path():
    """The ledger path — lazy so importing this module never
    touches the data dir (the snooze_email precedent)."""
    from .config import DATA_DIR

    return DATA_DIR / _ALERTS_FILE


def _load_alerts(path=None) -> dict:
    """The dedupe ledger as {budget: {...}, price: {...},
    subscription: {...}}. Missing / corrupt / wrong-type -> the
    empty ledger, never a raise."""
    target = Path(path) if path is not None else _default_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/corrupt = no alerts fired
        return {"budget": {}, "price": {}, "subscription": {}}
    if not isinstance(data, dict):
        return {"budget": {}, "price": {}, "subscription": {}}
    out = {"budget": {}, "price": {}, "subscription": {}}
    for kind in out:
        section = data.get(kind)
        if isinstance(section, dict):
            out[kind] = section
    return out


def _save_alerts(ledger: dict, path=None) -> bool:
    """Atomic whole-ledger write (A3-atomic): mkstemp + os.replace,
    then a read-back that must parse to a dict — True only when
    the bytes landed and read back clean. Callers check the return
    and report failure honestly."""
    target = Path(path) if path is not None else _default_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=target.name + ".",
            suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(ledger, indent=1))
            os.replace(tmp_name, target)
        except Exception:  # noqa: BLE001
            try:
                os.unlink(tmp_name)
            except OSError:  # noqa: BLE001 — best effort cleanup
                pass
            raise
        # Read-back: the claim is only made when the ledger parses.
        check = json.loads(target.read_text(encoding="utf-8"))
        return isinstance(check, dict)
    except Exception:  # noqa: BLE001
        return False


def _over_budget_lines(entries) -> list:
    """One line per envelope that is over budget right now."""
    lines = []
    for entry in entries:
        budget, spent = entry["budget"], entry["spent"]
        if spent > budget:
            lines.append(
                f"Budget alert — '{entry['key']}' is "
                f"{_money(spent - budget)} OVER budget "
                f"(spent {_money(spent)} of {_money(budget)}).")
    return lines


def _renewal_lines(entries, today) -> list:
    """One line per subscription renewing within the window."""
    lines = []
    for entry in entries:
        day = entry.get("renewal_day")
        if not day:
            continue
        nxt = _next_renewal(day, today)
        if nxt is None:
            continue
        delta = (nxt - today).days
        if 0 <= delta <= RENEWAL_WINDOW_DAYS:
            when = "today" if delta == 0 else (
                f"in {delta} day{'s' if delta != 1 else ''} "
                f"({nxt.isoformat()})")
            lines.append(
                f"Renewal alert — '{entry['key']}' renews {when} "
                f"at {_money(entry['monthly'])} a month.")
    return lines


def _price_lines(watches) -> list:
    """One line per watch whose LAST-KNOWN price is at or under
    target. No fetch — this is the briefing shape, so a stale
    price is labelled with when it was seen, never presented as
    live."""
    lines = []
    for url, entry in watches.items():
        target = entry.get("target")
        last = entry.get("last_price")
        if not target or last is None:
            continue
        try:
            price = float(last)
        except (TypeError, ValueError):
            continue
        if price <= target:
            seen = entry.get("last_checked") or "unknown time"
            display = entry.get("last_display") or f"{price:,.2f}"
            lines.append(
                f"Price alert — {url} was last seen at {display} "
                f"(at {seen}) — at or under your target of "
                f"{target:,.2f}.")
    return lines


def money_alert_lines(now=None) -> list:
    """STANDING money alerts right now, as lines. Pure read —
    no fetch, no ledger write. `now` is injectable so tests can
    drive it with a fixed date; defaults to today, local."""
    today = (now.date() if isinstance(now, datetime.datetime)
             else now) if now is not None else (
        datetime.datetime.now().astimezone().date())
    try:
        entries, _broken = _load_envelopes()
    except Exception:  # noqa: BLE001
        entries = []
    try:
        subs, _broken_sub = _load_subscriptions()
    except Exception:  # noqa: BLE001
        subs = []
    try:
        watches = _load_prices()
    except Exception:  # noqa: BLE001
        watches = {}
    lines = []
    lines += _over_budget_lines(entries)
    lines += _renewal_lines(subs, today)
    lines += _price_lines(watches)
    return lines


def money_alert_lines_for_briefing(now=None) -> list:
    """The briefing's money section, as lines — the standing
    alerts, never raising. On a money-read failure the section
    carries one honest line instead of going silently missing."""
    try:
        return money_alert_lines(now=now)
    except Exception:  # noqa: BLE001 — the briefing must not fail on money
        return ["Money alerts couldn't be read just now."]


def _fetch_price(url: str):
    """(price, display, error): the first price on the page, or
    (None, '', error). The price_watch check shape, without the
    prose."""
    body, _resp, err = _fetch(url, _PAGE_CAP)
    if body is None:
        return None, "", f"Couldn't fetch {url} — {err}."
    text = _visible_text(body.decode("utf-8", errors="replace"))
    found = _first_price(text)
    if found is None:
        return None, "", (f"No price found on {url} — the page "
                          "may show prices only after a login, or "
                          "draw them with scripts I can't run.")
    amount, display = found
    return amount, display, ""


def check_money_alerts() -> str:
    """Check the money alerts and report the NEWLY-DUE ones.

    Over-budget envelopes, price watches at or under target
    (pages fetched now), subscriptions renewing within 7 days.
    Each alert fires once until its numbers change — the ledger
    (`data/money_alerts.json`) records what fired — so a standing
    condition never nags. A failed price fetch is named honestly
    in the answer, never hidden and never counted as an alert.
    Never raises: a store failure returns the failure in plain
    words."""
    today = datetime.datetime.now().astimezone().date()
    try:
        entries, _broken = _load_envelopes()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the budget envelopes: {e}"
    try:
        subs, _broken_sub = _load_subscriptions()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the subscriptions: {e}"
    try:
        watches = _load_prices()
    except Exception:  # noqa: BLE001
        watches = {}
    ledger = _load_alerts()
    due = []
    failed = []

    # -- budgets: re-fire when the numbers move, clear when back under.
    for entry in entries:
        key, budget, spent = entry["key"], entry["budget"], entry["spent"]
        prev = ledger["budget"].get(key) or {}
        if spent > budget:
            if (prev.get("spent") != spent
                    or prev.get("budget") != budget):
                due.append(
                    f"Budget alert — '{key}' is "
                    f"{_money(spent - budget)} OVER budget "
                    f"(spent {_money(spent)} of {_money(budget)}).")
            ledger["budget"][key] = {"spent": spent, "budget": budget}
        else:
            ledger["budget"].pop(key, None)

    # -- price watches: fetch each, re-fire on a new low below target
    #    or a changed target, clear when back above target.
    for url, entry in list(watches.items()):
        target = entry.get("target")
        if not target:
            continue
        price, display, err = _fetch_price(url)
        if price is None:
            failed.append(err)
            continue
        entry["last_price"] = price
        entry["last_display"] = display
        entry["last_checked"] = datetime.datetime.now().isoformat(
            timespec="seconds")
        prev = ledger["price"].get(url) or {}
        if price <= target:
            if (prev.get("price") is None
                    or price < float(prev["price"])
                    or prev.get("target") != target):
                due.append(
                    f"Price alert — {url} is at {display} — at or "
                    f"under your target of {target:,.2f}.")
            ledger["price"][url] = {"price": price, "target": target}
        else:
            ledger["price"].pop(url, None)
    if watches:
        if not _save_prices(watches):
            failed.append("The price-watch list couldn't be saved, "
                          "so the last-seen prices may be stale.")

    # -- subscriptions: re-fire only for a new renewal date.
    for entry in subs:
        day = entry.get("renewal_day")
        if not day:
            continue
        nxt = _next_renewal(day, today)
        if nxt is None:
            continue
        delta = (nxt - today).days
        if 0 <= delta <= RENEWAL_WINDOW_DAYS:
            iso = nxt.isoformat()
            if ledger["subscription"].get(entry["key"]) != iso:
                when = "today" if delta == 0 else (
                    f"in {delta} day{'s' if delta != 1 else ''} "
                    f"({iso})")
                due.append(
                    f"Renewal alert — '{entry['key']}' renews "
                    f"{when} at {_money(entry['monthly'])} a month.")
            ledger["subscription"][entry["key"]] = iso
        else:
            ledger["subscription"].pop(entry["key"], None)

    if not _save_alerts(ledger):
        return ("I checked the money alerts but couldn't save the "
                "alert record, so I can't tell you what's new "
                "without repeating old alerts — try again in a "
                "moment.")
    out = []
    if due:
        out.append(f"Money alerts ({len(due)}):")
        out += [f"• {line}" for line in due]
    else:
        out.append("No money alerts right now.")
    if failed:
        out.append("Checks that couldn't run:")
        out += [f"• {line}" for line in failed]
    return "\n".join(out)
