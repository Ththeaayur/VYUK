"""Event planning dashboard (R16-8, the-wedding-website inspiration).

A wedding / event planning dashboard as own stdlib code — a plan
store (one memory fact per plan) + six funnel tools. Nothing is
adopted from drakebridgewater/the-wedding-website (Apache-2.0 per
the LICENSE file from master; the GitHub API reports NOASSERTION):
that repo is a Django 4.2 + React 18 app with a hosted public
site, per-party invitation links, guest-submitted RSVPs, a
honeymoon fund and a Google Sheets export. The build takes only
the planner SHAPE as inspiration (plan / guests / vendors /
budget + estimator / milestones / seating / run-of-show / notes)
and rebuilds it over the tree's own shapes; it drops the Django
+ React app entirely.

What existed before this batch (all read on the backup first):

  * Money calculators only, all stateless and caller-supplied:
    budget_envelope (core/skills_money_budget.py:68 — named
    monthly budgets as memory facts), expense_log
    (core/skills_productivity5_logs.py:164), split_bill /
    rent_split / tip_and_tax. No budget ESTIMATOR (no per-guest
    rate x guest count), no vendor store, no vendor comparison.
  * gift_log (core/skills_admin_lend_assets.py:41) stores one
    idea per person — no guest list, no party size, no meal
    choice, no table, no RSVP state.
  * The scheduler (core/scheduler.py — ProactiveScheduler +
    add_reminder) queues reminders; nothing wires a dated
    checklist item to one.
  * The task ledger (core/brain/task_ledger.py — TaskLedger)
    tracks agent goals/tasks, not a user's dated event
    checklist; milestones.py (core/milestones.py) logs firsts
    (first launch, first Live connect), not event milestones.
  * Zero guest-list / seating / vendor-comparison / budget-
    estimator / run-of-show surface anywhere in core/ or gui/
    (parent-verified full-list greps: wedding/rsvp hits are
    story prompts + one trigger word; seating 0 files; vendor
    hits are the trust-pack vendor-counters + app adapters;
    milestone hits are the firsts log).

What was genuinely missing — the remainder this module builds,
and only this remainder:

  * An event PLAN store: name, date, expected guests, tier,
    total budget, guests, vendors, milestones, run-of-show,
    notes — one memory fact per plan (the budget_envelope
    shape: category "event_plans", key = plan name, value =
    the plan JSON).
  * A GUEST list with party size, meal choice, table and RSVP
    state recorded by the owner (no guest ever touches the
    product — RSVP *collection* through a link is the public-
    site half, not built here).
  * A VENDOR store (category, name, quote) + a comparison
    (cheapest / dearest / spread / average per category).
  * A budget ESTIMATOR: per-guest tier rate x guest count +
    vendor quotes, with the plan's budget beside it — plus a
    summary (quoted total, estimate, remaining vs budget).
  * Dated MILESTONES with a done state, optionally wiring a
    scheduler reminder (opt-in, remind=true, fires 09:00 on
    the due date).
  * A run-of-show (time + title, sorted by time) and plan
    NOTES (the idea board, as notes on the plan).

What is NOT built here, and why:

  * The PUBLIC wedding website (countdown, save-the-date,
    wedding-party page, day-of program, guestbook, moderated
    Q&A, name-gated guest content). A shareable page needs a
    hosting / publishing shape VYUK does not have (no_feed
    persists; update-feed hosting undecided) — NEEDS RULING
    (hosting/share shape), named as the follow-up, not built.
  * RSVP *collection* (per-party invitation links, guests
    submitting meal choices, invitation-opened timestamps).
    Guests responding means other people interacting with the
    product — the multi-user / guest shape (BZ-7, parked).
    The owner recording an RSVP they were told is here; the
    link flow is not.
  * The honeymoon fund (guests paying into a fund). Money
    movement — ruling 34 territory, OPEN. Not built.
  * Google Sheets export. No Sheets surface in the tree and
    no export format is owed by the dashboard; a CSV export
    is a small follow-up once a sheet target is named.
  * A drag-and-drop seating GUI. Seating here is a data
    model (guest -> table) + a tool; the GUI surface is a
    follow-up. VYUK is voice-first; the tools are the build.

Trust discipline (stated plainly, same shape as the money
tools this builds on):

  * Local only: every write lands in the app's own memory
    store (facts, category "event_plans") — no network, no
    egress, no second app, no new dependency (stdlib only).
  * Free by design ("app-state", the budget_envelope /
    expense_log / gift_log precedent): the tools write only
    to VYUK's own store, so they ask nothing; event_budget
    is a pure read. No money moves anywhere here — quotes
    are numbers the owner types in, never payments.
  * Every write is verified before it is claimed (A1-store):
    save_fact returns True only when its own read-back proves
    the row landed, and the handler re-reads the plan and
    confirms the added record is present — a write that does
    not verify is reported as a failure, never claimed.
  * The estimator's tier rates are rough PLANNING defaults
    (modest 800 / standard 2000 / premium 5000 / luxury
    12000 per guest, in the plan's own currency units — not
    quotes, not a price feed); the caller overrides the rate
    with per_guest whenever they know their venue's figure.
"""

from __future__ import annotations

import datetime
import json

from .skills_money_helpers import _coerce_float, _money, _store

_PLANS_CATEGORY = "event_plans"

_TIER_RATES = {
    "modest": 800.0,
    "standard": 2000.0,
    "premium": 5000.0,
    "luxury": 12000.0,
}
_DEFAULT_TIER = "standard"

_RSVP_VALUES = ("yes", "no", "maybe", "pending")

_MAX_NAME_CHARS = 200
_MAX_TITLE_CHARS = 200
_MAX_NOTE_CHARS = 500
_MAX_MEAL_CHARS = 100
_MAX_TABLE_CHARS = 50
_MAX_CATEGORY_CHARS = 100
_MAX_GUESTS = 500
_MAX_VENDORS = 200
_MAX_MILESTONES = 200
_MAX_SCHEDULE = 200
_MAX_NOTES = 100


# ---------------------------------------------------------------------------
# Store — one memory fact per plan (the budget_envelope shape)
# ---------------------------------------------------------------------------


def _get_scheduler():
    """The production scheduler, resolved lazily (seam: tests
    substitute a temp ProactiveScheduler by replacing this
    function; handlers never touch the singleton directly)."""
    from .scheduler import scheduler

    return scheduler


def _load_plans() -> tuple[dict, int]:
    """(plans by lower-cased name, broken_count): every stored
    plan fact parsed; unparseable or misshapen ones counted."""
    plans: dict[str, dict] = {}
    broken = 0
    try:
        facts = [f for f in _store().get_all_facts()
                 if f.get("category") == _PLANS_CATEGORY]
    except Exception:
        return plans, 1
    for fact in facts:
        try:
            plan = json.loads(fact.get("value") or "")
            if not isinstance(plan, dict):
                raise ValueError("plan is not an object")
            for key in ("name", "guests", "vendors", "milestones",
                        "schedule", "notes"):
                if key not in plan:
                    raise ValueError(f"plan is missing {key!r}")
            for key in ("guests", "vendors", "milestones",
                        "schedule", "notes"):
                if not isinstance(plan[key], list):
                    raise ValueError(f"plan {key!r} is not a list")
            plans[str(plan["name"]).strip().lower()] = plan
        except Exception:  # noqa: BLE001 — one broken fact must not
            broken += 1    # take the whole dashboard down
    return plans, broken


def _save_plan(plan: dict) -> bool:
    """Persist one plan and verify it landed (A1-store):
    save_fact returns True only when its own read-back proves
    the row landed, and the re-read here must parse back to the
    same plan name. False means the write did not verify."""
    key = str(plan.get("name") or "").strip()
    if not key:
        return False
    try:
        landed = _store().save_fact(
            _PLANS_CATEGORY, key, json.dumps(plan))
        if not landed:
            return False
        back = _store().get_fact(_PLANS_CATEGORY, key)
        if back is None:
            return False
        reread = json.loads(back.get("value") or "")
        return (isinstance(reread, dict)
                and str(reread.get("name") or "").strip() == key)
    except Exception:  # noqa: BLE001
        return False


def _find_plan(plans: dict, name: str):
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    return plans.get(wanted)


def _new_plan(name: str, date: str, guests_expected: int,
              tier: str, budget_total: float) -> dict:
    return {
        "name": name,
        "date": date,
        "guests_expected": guests_expected,
        "tier": tier,
        "budget_total": budget_total,
        "guests": [],
        "vendors": [],
        "milestones": [],
        "schedule": [],
        "notes": [],
    }


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


def _parse_date(text: str):
    try:
        return datetime.date.fromisoformat(str(text or "").strip())
    except ValueError:
        return None


def _parse_time(text: str):
    parts = str(text or "").strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return None


def _broken_note(broken: int) -> str:
    if not broken:
        return ""
    return (f" ({broken} stored plan"
            f"{'s' if broken != 1 else ''} couldn't be read and "
            f"{'were' if broken != 1 else 'was'} skipped.)")


def _plan_line(plan: dict) -> str:
    guests = len(plan.get("guests") or [])
    headcount = sum(int(g.get("party_size") or 1)
                    for g in (plan.get("guests") or []))
    vendors = len(plan.get("vendors") or [])
    quoted = sum(float(v.get("quote") or 0)
                 for v in (plan.get("vendors") or []))
    open_ms = sum(1 for m in (plan.get("milestones") or [])
                  if not m.get("done"))
    done_ms = len(plan.get("milestones") or []) - open_ms
    return (f"• {plan.get('name')} — {plan.get('date') or 'no date'}, "
            f"{guests} guest entries ({headcount} people), "
            f"{vendors} vendors ({_money(quoted)} quoted), "
            f"{open_ms} open + {done_ms} done milestones, "
            f"budget {_money(float(plan.get('budget_total') or 0))}")


# ---------------------------------------------------------------------------
# event_plan — create / summary / add_note
# ---------------------------------------------------------------------------


def event_plan(action: str = "", name: str = "", date: str = "",
               guests_expected=0, tier: str = "", budget_total=0,
               note: str = "") -> str:
    """Create an event plan, show one (or list all), or add a note
    (the idea board) to one. Plans live in the app's own store."""
    action = str(action or "").strip().lower()
    if action not in ("create", "summary", "add_note"):
        return ("Event-plan actions are 'create', 'summary' and "
                "'add_note' — e.g. event_plan with action 'create', "
                "a name like 'Spring wedding' and a date.")
    plans, broken = _load_plans()
    broken_note = _broken_note(broken)
    if action == "summary" and not str(name or "").strip():
        if not plans:
            return ("No event plans yet. Create one with "
                    f"event_plan, action 'create' and a name.{broken_note}")
        lines = [f"Event plans ({len(plans)}):{broken_note}"]
        for key in sorted(plans):
            lines.append(_plan_line(plans[key]))
        return "\n".join(lines)
    clean_name = _clean(name, _MAX_NAME_CHARS)
    if not clean_name:
        return "That needs the plan's name."
    if action == "create":
        if _find_plan(plans, clean_name) is not None:
            return (f"There is already a plan called "
                    f"'{clean_name}'. Pick a different name, or ask "
                    f"for its summary.{broken_note}")
        clean_date = str(date or "").strip()
        if clean_date and _parse_date(clean_date) is None:
            return (f"'{clean_date}' isn't a date — use YYYY-MM-DD, "
                    f"e.g. 2027-02-14. Nothing was created.")
        expected = _coerce_int(guests_expected)
        if expected is None or expected < 0:
            return ("The expected guest count needs to be zero or "
                    "more. Nothing was created.")
        clean_tier = str(tier or "").strip().lower() or _DEFAULT_TIER
        if clean_tier not in _TIER_RATES:
            return (f"'{clean_tier}' isn't a tier — tiers are "
                    f"{', '.join(sorted(_TIER_RATES))}. Nothing was "
                    f"created.")
        budget = _coerce_float(budget_total)
        if budget is None or budget < 0:
            return ("The total budget needs to be zero or more. "
                    "Nothing was created.")
        plan = _new_plan(clean_name, clean_date, expected,
                         clean_tier, budget)
        if not _save_plan(plan):
            return (f"Couldn't save the plan '{clean_name}' — the "
                    f"write did not verify. Nothing was created.")
        return (f"Created the plan '{clean_name}'"
                f"{' for ' + clean_date if clean_date else ''} — "
                f"{expected} guests expected, tier '{clean_tier}', "
                f"budget {_money(budget)}.{broken_note}")
    plan = _find_plan(plans, clean_name)
    if plan is None:
        return (f"No plan called '{clean_name}'. Create it first "
                f"with event_plan, action 'create'.{broken_note}")
    if action == "summary":
        lines = [_plan_line(plan)]
        notes = plan.get("notes") or []
        if notes:
            lines.append(f"Notes ({len(notes)}):")
            lines += [f"  • {n}" for n in notes]
        if broken:
            lines.append(broken_note.strip())
        return "\n".join(lines)
    clean_note = _clean(note, _MAX_NOTE_CHARS)
    if not clean_note:
        return "That needs the note's text."
    if len(plan.get("notes") or []) >= _MAX_NOTES:
        return (f"The plan '{clean_name}' already holds "
                f"{_MAX_NOTES} notes — the cap. Nothing was added.")
    plan.setdefault("notes", []).append(clean_note)
    if not _save_plan(plan):
        return (f"Couldn't save the note on '{clean_name}' — the "
                f"write did not verify. Nothing was added.")
    reread, _ = _load_plans()
    back = _find_plan(reread, clean_name)
    if back is None or clean_note not in (back.get("notes") or []):
        return (f"The note was written but the re-read could not "
                f"find it on '{clean_name}' — treat it as not added.")
    return (f"Added the note to '{clean_name}': "
            f"'{clean_note}'.{broken_note}")


# ---------------------------------------------------------------------------
# event_guest — add / set_table / list (seating is the table field)
# ---------------------------------------------------------------------------


def event_guest(action: str = "", plan: str = "", name: str = "",
                party_size=1, meal: str = "", table: str = "",
                rsvp: str = "") -> str:
    """Add a guest to a plan (party size, meal choice, table,
    RSVP state), move a guest to another table, or list a plan's
    guests. The owner records RSVPs they were told — no guest
    ever touches the product through here."""
    action = str(action or "").strip().lower()
    if action not in ("add", "set_table", "list"):
        return ("Event-guest actions are 'add', 'set_table' and "
                "'list' — e.g. event_guest with action 'add', a "
                "plan, and a guest name.")
    plans, broken = _load_plans()
    broken_note = _broken_note(broken)
    found = _find_plan(plans, plan)
    if found is None:
        return (f"No plan called '{str(plan or '').strip()}'. "
                f"Create it first with event_plan, action "
                f"'create'.{broken_note}")
    if action == "list":
        guests = found.get("guests") or []
        if not guests:
            return (f"No guests on '{found.get('name')}' yet. Add "
                    f"one with event_guest, action 'add'.{broken_note}")
        lines = [f"Guests on '{found.get('name')}' "
                 f"({len(guests)} entries):{broken_note}"]
        for g in sorted(guests, key=lambda x: str(x.get("name") or "").lower()):
            lines.append(
                f"• {g.get('name')} — party of "
                f"{int(g.get('party_size') or 1)}"
                f"{', meal: ' + g['meal'] if g.get('meal') else ''}"
                f"{', table ' + g['table'] if g.get('table') else ', no table yet'}"
                f", RSVP: {g.get('rsvp') or 'pending'}")
        return "\n".join(lines)
    clean_name = _clean(name, _MAX_NAME_CHARS)
    if not clean_name:
        return "That needs the guest's name."
    guests = found.setdefault("guests", [])
    match = None
    for g in guests:
        if str(g.get("name") or "").strip().lower() == clean_name.lower():
            match = g
            break
    if action == "add":
        if match is not None:
            return (f"'{clean_name}' is already a guest on "
                    f"'{found.get('name')}'. Use set_table to move "
                    f"them.{broken_note}")
        if len(guests) >= _MAX_GUESTS:
            return (f"The plan already holds {_MAX_GUESTS} guests — "
                    f"the cap. Nothing was added.")
        size = _coerce_int(party_size)
        if size is None or size < 1:
            return ("The party size needs to be 1 or more. Nothing "
                    "was added.")
        clean_rsvp = str(rsvp or "").strip().lower() or "pending"
        if clean_rsvp not in _RSVP_VALUES:
            return (f"'{clean_rsvp}' isn't an RSVP state — use "
                    f"{', '.join(_RSVP_VALUES)}. Nothing was added.")
        guests.append({
            "name": clean_name,
            "party_size": size,
            "meal": _clean(meal, _MAX_MEAL_CHARS),
            "table": _clean(table, _MAX_TABLE_CHARS),
            "rsvp": clean_rsvp,
        })
        if not _save_plan(found):
            guests.pop()
            return (f"Couldn't save '{clean_name}' on "
                    f"'{found.get('name')}' — the write did not "
                    f"verify. Nothing was added.")
        reread, _ = _load_plans()
        back = _find_plan(reread, found.get("name") or "")
        names = [str(g.get("name") or "").strip().lower()
                 for g in (back.get("guests") or [])] if back else []
        if clean_name.lower() not in names:
            return (f"'{clean_name}' was written but the re-read "
                    f"could not find them on "
                    f"'{found.get('name')}' — treat it as not added.")
        return (f"Added '{clean_name}' to '{found.get('name')}' — "
                f"party of {size}, RSVP: {clean_rsvp}"
                f"{', table ' + _clean(table, _MAX_TABLE_CHARS) if str(table or '').strip() else ''}."
                f"{broken_note}")
    clean_table = _clean(table, _MAX_TABLE_CHARS)
    if match is None:
        return (f"'{clean_name}' is not a guest on "
                f"'{found.get('name')}'. Add them first.{broken_note}")
    if not clean_table:
        return "That needs the table — e.g. '3' or 'Top table'."
    old_table = match.get("table") or ""
    match["table"] = clean_table
    if not _save_plan(found):
        match["table"] = old_table
        return (f"Couldn't move '{clean_name}' — the write did not "
                f"verify. Nothing changed.")
    reread, _ = _load_plans()
    back = _find_plan(reread, found.get("name") or "")
    moved = None
    if back:
        for g in (back.get("guests") or []):
            if str(g.get("name") or "").strip().lower() == clean_name.lower():
                moved = g
                break
    if moved is None or (moved.get("table") or "") != clean_table:
        match["table"] = old_table
        return (f"'{clean_name}' was written but the re-read did "
                f"not find them at table '{clean_table}' — treat "
                f"it as not moved.")
    return (f"Moved '{clean_name}' to table '{clean_table}' on "
            f"'{found.get('name')}'."
            f"{' (was ' + old_table + ')' if old_table else ''}"
            f"{broken_note}")


# ---------------------------------------------------------------------------
# event_vendor — add / compare / list
# ---------------------------------------------------------------------------


def event_vendor(action: str = "", plan: str = "", category: str = "",
                 name: str = "", quote=0) -> str:
    """Add a vendor quote to a plan, compare quotes in one
    category (cheapest / dearest / spread / average), or list a
    plan's vendors. Quotes are numbers the owner types in —
    never payments, never a price feed."""
    action = str(action or "").strip().lower()
    if action not in ("add", "compare", "list"):
        return ("Event-vendor actions are 'add', 'compare' and "
                "'list' — e.g. event_vendor with action 'compare', "
                "a plan, and a category like 'venue'.")
    plans, broken = _load_plans()
    broken_note = _broken_note(broken)
    found = _find_plan(plans, plan)
    if found is None:
        return (f"No plan called '{str(plan or '').strip()}'. "
                f"Create it first with event_plan, action "
                f"'create'.{broken_note}")
    vendors = found.setdefault("vendors", [])
    if action == "list":
        if not vendors:
            return (f"No vendors on '{found.get('name')}' yet. Add "
                    f"one with event_vendor, action 'add'.{broken_note}")
        by_cat: dict[str, list] = {}
        for v in vendors:
            by_cat.setdefault(str(v.get("category") or "uncategorised"),
                              []).append(v)
        lines = [f"Vendors on '{found.get('name')}' "
                 f"({len(vendors)}):{broken_note}"]
        for cat in sorted(by_cat):
            lines.append(f"  {cat}:")
            for v in sorted(by_cat[cat],
                            key=lambda x: float(x.get("quote") or 0)):
                lines.append(f"    • {v.get('name')} — "
                             f"{_money(float(v.get('quote') or 0))}")
        lines.append(f"Quoted total: "
                     f"{_money(sum(float(v.get('quote') or 0) for v in vendors))}.")
        return "\n".join(lines)
    clean_cat = _clean(category, _MAX_CATEGORY_CHARS)
    if not clean_cat:
        return "That needs the vendor category — e.g. 'venue' or 'catering'."
    if action == "compare":
        rows = [v for v in vendors
                if str(v.get("category") or "").strip().lower()
                == clean_cat.lower()]
        if not rows:
            return (f"No '{clean_cat}' vendors on "
                    f"'{found.get('name')}' yet.{broken_note}")
        rows.sort(key=lambda x: float(x.get("quote") or 0))
        cheapest, dearest = rows[0], rows[-1]
        total = sum(float(v.get("quote") or 0) for v in rows)
        lines = [f"'{clean_cat}' quotes on '{found.get('name')}' "
                 f"({len(rows)}):{broken_note}"]
        for v in rows:
            lines.append(f"• {v.get('name')} — "
                         f"{_money(float(v.get('quote') or 0))}")
        lines.append(f"Cheapest: {cheapest.get('name')} at "
                     f"{_money(float(cheapest.get('quote') or 0))}.")
        if len(rows) > 1:
            lines.append(
                f"Dearest: {dearest.get('name')} at "
                f"{_money(float(dearest.get('quote') or 0))} — spread "
                f"{_money(float(dearest.get('quote') or 0) - float(cheapest.get('quote') or 0))}, "
                f"average {_money(total / len(rows))}.")
        return "\n".join(lines)
    clean_name = _clean(name, _MAX_NAME_CHARS)
    if not clean_name:
        return "That needs the vendor's name."
    for v in vendors:
        if (str(v.get("category") or "").strip().lower()
                == clean_cat.lower()
                and str(v.get("name") or "").strip().lower()
                == clean_name.lower()):
            return (f"'{clean_name}' is already a '{clean_cat}' "
                    f"vendor on '{found.get('name')}'.{broken_note}")
    if len(vendors) >= _MAX_VENDORS:
        return (f"The plan already holds {_MAX_VENDORS} vendors — "
                f"the cap. Nothing was added.")
    value = _coerce_float(quote)
    if value is None or value <= 0:
        return ("The quote needs to be a number more than zero. "
                "Nothing was added.")
    vendors.append({"category": clean_cat, "name": clean_name,
                    "quote": value})
    if not _save_plan(found):
        vendors.pop()
        return (f"Couldn't save '{clean_name}' on "
                f"'{found.get('name')}' — the write did not verify. "
                f"Nothing was added.")
    reread, _ = _load_plans()
    back = _find_plan(reread, found.get("name") or "")
    names = [(str(v.get("category") or "").strip().lower(),
              str(v.get("name") or "").strip().lower())
             for v in (back.get("vendors") or [])] if back else []
    if (clean_cat.lower(), clean_name.lower()) not in names:
        return (f"'{clean_name}' was written but the re-read could "
                f"not find them on '{found.get('name')}' — treat it "
                f"as not added.")
    return (f"Added '{clean_name}' ({clean_cat}) to "
            f"'{found.get('name')}' — quote "
            f"{_money(value)}.{broken_note}")


# ---------------------------------------------------------------------------
# event_budget — estimate / summary (pure read)
# ---------------------------------------------------------------------------


def _estimate(plan: dict, tier: str = "", guests=None,
              per_guest=0) -> tuple[float, float, float, int, str]:
    """(per_guest_rate, guest_cost, quote_total, guest_count,
    tier_used): the estimate is a tier rate x guests plus the
    vendor quotes. Rates are rough planning defaults — the
    caller overrides with per_guest whenever they know their
    venue's figure."""
    clean_tier = str(tier or "").strip().lower() or str(
        plan.get("tier") or "").strip().lower() or _DEFAULT_TIER
    if clean_tier not in _TIER_RATES:
        clean_tier = _DEFAULT_TIER
    override = _coerce_float(per_guest)
    rate = override if (override is not None and override > 0) \
        else _TIER_RATES[clean_tier]
    count = _coerce_int(guests)
    if count is None or count <= 0:
        # 0 means "not given" (the handler default) — fall back to
        # the plan's expected count, then to the added headcount.
        count = int(plan.get("guests_expected") or 0)
        if count <= 0:
            count = sum(int(g.get("party_size") or 1)
                        for g in (plan.get("guests") or []))
    quotes = sum(float(v.get("quote") or 0)
                 for v in (plan.get("vendors") or []))
    return rate, rate * count, quotes, count, clean_tier


def event_budget(action: str = "", plan: str = "", tier: str = "",
                 guests=0, per_guest=0) -> str:
    """Estimate an event's cost (per-guest tier rate x guest
    count + vendor quotes) or summarise the budget picture.
    Pure computation over the stored plan — nothing is written,
    nothing is fetched, no price feed exists."""
    action = str(action or "").strip().lower()
    if action not in ("estimate", "summary"):
        return ("Event-budget actions are 'estimate' and 'summary' "
                "— e.g. event_budget with action 'estimate' and a "
                "plan name.")
    plans, broken = _load_plans()
    broken_note = _broken_note(broken)
    found = _find_plan(plans, plan)
    if found is None:
        return (f"No plan called '{str(plan or '').strip()}'. "
                f"Create it first with event_plan, action "
                f"'create'.{broken_note}")
    rate, guest_cost, quotes, count, tier_used = _estimate(
        found, tier, guests, per_guest)
    budget = float(found.get("budget_total") or 0)
    total = guest_cost + quotes
    if action == "estimate":
        lines = [f"Estimate for '{found.get('name')}':{broken_note}",
                 f"• Guests: {count} x {_money(rate)} "
                 f"(tier '{tier_used}') = {_money(guest_cost)}",
                 f"• Vendor quotes: {_money(quotes)}",
                 f"• Estimated total: {_money(total)}"]
        if budget > 0:
            if total <= budget:
                lines.append(f"• Budget: {_money(budget)} — "
                             f"{_money(budget - total)} left.")
            else:
                lines.append(f"• Budget: {_money(budget)} — "
                             f"{_money(total - budget)} OVER budget.")
        lines.append("Tier rates are rough planning defaults — pass "
                     "per_guest with your venue's actual figure for "
                     "a real number.")
        return "\n".join(lines)
    by_cat: dict[str, float] = {}
    for v in (found.get("vendors") or []):
        cat = str(v.get("category") or "uncategorised")
        by_cat[cat] = by_cat.get(cat, 0.0) + float(v.get("quote") or 0)
    lines = [f"Budget for '{found.get('name')}':{broken_note}",
             f"• Total budget: {_money(budget)}",
             f"• Vendor quotes: {_money(quotes)}"
             + (f" ({len(found.get('vendors') or [])} vendors)"
                if found.get("vendors") else " (no vendors yet)"),
             f"• Estimated total (guests + quotes): {_money(total)}"]
    for cat in sorted(by_cat):
        lines.append(f"  • {cat}: {_money(by_cat[cat])}")
    if budget > 0:
        if total <= budget:
            lines.append(f"• {_money(budget - total)} left of budget.")
        else:
            lines.append(f"• {_money(total - budget)} OVER budget.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# event_milestone — add / complete / list (+ opt-in scheduler reminder)
# ---------------------------------------------------------------------------


def event_milestone(action: str = "", plan: str = "", title: str = "",
                    due: str = "", remind=False) -> str:
    """Add a dated milestone to a plan, mark one done, or list a
    plan's milestones. With remind=true and a future due date, a
    scheduler reminder fires at 09:00 on the due date — the
    reminder is opt-in, never silent."""
    action = str(action or "").strip().lower()
    if action not in ("add", "complete", "list"):
        return ("Event-milestone actions are 'add', 'complete' and "
                "'list' — e.g. event_milestone with action 'add', a "
                "plan, a title and a due date.")
    plans, broken = _load_plans()
    broken_note = _broken_note(broken)
    found = _find_plan(plans, plan)
    if found is None:
        return (f"No plan called '{str(plan or '').strip()}'. "
                f"Create it first with event_plan, action "
                f"'create'.{broken_note}")
    milestones = found.setdefault("milestones", [])
    if action == "list":
        if not milestones:
            return (f"No milestones on '{found.get('name')}' yet. "
                    f"Add one with event_milestone, action "
                    f"'add'.{broken_note}")
        ordered = sorted(
            milestones,
            key=lambda m: (str(m.get("due") or "9999"),
                           str(m.get("title") or "").lower()))
        lines = [f"Milestones on '{found.get('name')}' "
                 f"({len(milestones)}):{broken_note}"]
        for m in ordered:
            mark = "done" if m.get("done") else "open"
            lines.append(f"• [{'x' if m.get('done') else ' '}] "
                         f"{m.get('title')}"
                         f"{' — due ' + m['due'] if m.get('due') else ''}"
                         f" ({mark})")
        return "\n".join(lines)
    clean_title = _clean(title, _MAX_TITLE_CHARS)
    if not clean_title:
        return "That needs the milestone's title."
    match = None
    for m in milestones:
        if str(m.get("title") or "").strip().lower() == clean_title.lower():
            match = m
            break
    if action == "complete":
        if match is None:
            return (f"No milestone '{clean_title}' on "
                    f"'{found.get('name')}'.{broken_note}")
        if match.get("done"):
            return (f"'{clean_title}' is already done on "
                    f"'{found.get('name')}'.{broken_note}")
        match["done"] = True
        if not _save_plan(found):
            match["done"] = False
            return (f"Couldn't mark '{clean_title}' done — the "
                    f"write did not verify. Nothing changed.")
        reread, _ = _load_plans()
        back = _find_plan(reread, found.get("name") or "")
        done = False
        if back:
            for m in (back.get("milestones") or []):
                if (str(m.get("title") or "").strip().lower()
                        == clean_title.lower() and m.get("done")):
                    done = True
        if not done:
            match["done"] = False
            return (f"'{clean_title}' was written but the re-read "
                    f"did not find it done — treat it as not done.")
        return (f"Marked '{clean_title}' done on "
                f"'{found.get('name')}'.{broken_note}")
    if match is not None:
        return (f"'{clean_title}' is already a milestone on "
                f"'{found.get('name')}'.{broken_note}")
    if len(milestones) >= _MAX_MILESTONES:
        return (f"The plan already holds {_MAX_MILESTONES} "
                f"milestones — the cap. Nothing was added.")
    clean_due = str(due or "").strip()
    due_date = None
    if clean_due:
        due_date = _parse_date(clean_due)
        if due_date is None:
            return (f"'{clean_due}' isn't a date — use YYYY-MM-DD. "
                    f"Nothing was added.")
        clean_due = due_date.isoformat()
    milestones.append({"title": clean_title, "due": clean_due,
                       "done": False})
    if not _save_plan(found):
        milestones.pop()
        return (f"Couldn't save '{clean_title}' on "
                f"'{found.get('name')}' — the write did not verify. "
                f"Nothing was added.")
    reread, _ = _load_plans()
    back = _find_plan(reread, found.get("name") or "")
    titles = [str(m.get("title") or "").strip().lower()
              for m in (back.get("milestones") or [])] if back else []
    if clean_title.lower() not in titles:
        return (f"'{clean_title}' was written but the re-read "
                f"could not find it on '{found.get('name')}' — "
                f"treat it as not added.")
    reminder_note = ""
    if remind and due_date is not None:
        fire_at = datetime.datetime.combine(
            due_date, datetime.time(9, 0))
        try:
            label = _get_scheduler().add_reminder(
                label=f"Event: {clean_title}",
                fire_at=fire_at,
                message=(f"Event milestone due today: {clean_title} "
                         f"({found.get('name')})"),
                repeat=False)
            reminder_note = f" Reminder set ({label}, 09:00)."
        except Exception as e:  # noqa: BLE001 — the milestone is
            reminder_note = (  # saved; only the reminder failed
                f" The reminder was not set ({e}).")
    elif remind:
        reminder_note = (" No reminder was set — a milestone needs "
                         "a due date for one.")
    return (f"Added milestone '{clean_title}' to "
            f"'{found.get('name')}'"
            f"{' — due ' + clean_due if clean_due else ''}."
            f"{reminder_note}{broken_note}")


# ---------------------------------------------------------------------------
# event_schedule — add / list (the day-of run-of-show)
# ---------------------------------------------------------------------------


def event_schedule(action: str = "", plan: str = "", time: str = "",
                   title: str = "") -> str:
    """Add a timed item to a plan's day-of run-of-show, or list
    it in time order. A run-of-show item is not a milestone: it
    is a time on the day itself, not a deadline before it."""
    action = str(action or "").strip().lower()
    if action not in ("add", "list"):
        return ("Event-schedule actions are 'add' and 'list' — e.g. "
                "event_schedule with action 'add', a plan, a time "
                "like '18:00' and a title.")
    plans, broken = _load_plans()
    broken_note = _broken_note(broken)
    found = _find_plan(plans, plan)
    if found is None:
        return (f"No plan called '{str(plan or '').strip()}'. "
                f"Create it first with event_plan, action "
                f"'create'.{broken_note}")
    items = found.setdefault("schedule", [])
    if action == "list":
        if not items:
            return (f"No run-of-show on '{found.get('name')}' yet. "
                    f"Add one with event_schedule, action "
                    f"'add'.{broken_note}")
        ordered = sorted(items, key=lambda i: str(i.get("time") or ""))
        lines = [f"Run-of-show for '{found.get('name')}' "
                 f"({len(items)}):{broken_note}"]
        lines += [f"• {i.get('time')} — {i.get('title')}"
                  for i in ordered]
        return "\n".join(lines)
    clean_title = _clean(title, _MAX_TITLE_CHARS)
    if not clean_title:
        return "That needs the item's title."
    clean_time = _parse_time(time)
    if clean_time is None:
        return ("That needs a time of day — HH:MM, e.g. '18:00'. "
                "Nothing was added.")
    if len(items) >= _MAX_SCHEDULE:
        return (f"The plan already holds {_MAX_SCHEDULE} run-of-show "
                f"items — the cap. Nothing was added.")
    items.append({"time": clean_time, "title": clean_title})
    if not _save_plan(found):
        items.pop()
        return (f"Couldn't save '{clean_title}' on "
                f"'{found.get('name')}' — the write did not verify. "
                f"Nothing was added.")
    reread, _ = _load_plans()
    back = _find_plan(reread, found.get("name") or "")
    pairs = [(str(i.get("time") or ""), str(i.get("title") or ""))
             for i in (back.get("schedule") or [])] if back else []
    if (clean_time, clean_title) not in pairs:
        return (f"'{clean_title}' was written but the re-read "
                f"could not find it on '{found.get('name')}' — "
                f"treat it as not added.")
    return (f"Added '{clean_title}' at {clean_time} to the "
            f"run-of-show for '{found.get('name')}'.{broken_note}")


# ---------------------------------------------------------------------------
# Registration — the module owns its catalogue via register_all
# (same pattern as the other packs)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register the event-planning pack (R16-8, the-wedding-website
    inspiration): event_plan / event_guest / event_vendor /
    event_milestone / event_schedule (free by design, "app-state"
    — they write only to VYUK's own plan store, the
    budget_envelope / expense_log precedent) + event_budget
    (read-only — pure computation over the stored plan). Own
    stdlib code over the tree's own money + task + scheduler
    shapes; nothing adopted — no Django, no React, no hosted
    site, no RSVP link flow, no honeymoon fund, no Sheets
    export. The module owns its catalogue via register_all
    (same pattern as the other packs)."""
    registry.register(
        name="event_plan",
        description="Create an event plan (a wedding, a party, any big day), show one, or add a note to one. A plan holds the date, expected guests, tier and total budget, plus its guests, vendors, milestones, run-of-show and notes. Use when the user starts planning an event or asks what is on a plan. Nothing leaves the machine — plans live in VYUK's own store.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "'create', 'summary' or 'add_note'"},
                "name": {"type": "string",
                         "description": "The plan's name, e.g. 'Spring wedding'"},
                "date": {"type": "string",
                         "description": "The event date, YYYY-MM-DD, e.g. '2027-02-14'"},
                "guests_expected": {"type": "integer",
                                    "description": "How many guests are expected"},
                "tier": {"type": "string",
                         "description": "Budget tier: modest, standard, premium or luxury"},
                "budget_total": {"type": "number",
                                 "description": "The total budget for the event"},
                "note": {"type": "string",
                         "description": "A note to add to the plan (the idea board)"},
            },
            "required": ["action"],
        },
        handler=event_plan,
    )
    registry.register(
        name="event_guest",
        description="Add a guest to an event plan (party size, meal choice, table, RSVP state), move a guest to another table, or list a plan's guests. The owner records RSVPs they were told — no guest ever touches the product through here. Use when the user adds guests, seats them, or asks who is coming.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "'add', 'set_table' or 'list'"},
                "plan": {"type": "string",
                         "description": "The plan's name"},
                "name": {"type": "string",
                         "description": "The guest's name"},
                "party_size": {"type": "integer",
                               "description": "How many people are in the guest's party (default 1)"},
                "meal": {"type": "string",
                         "description": "The guest's meal choice, e.g. 'veg'"},
                "table": {"type": "string",
                          "description": "The table to seat the guest at, e.g. '3'"},
                "rsvp": {"type": "string",
                         "description": "RSVP state: yes, no, maybe or pending"},
            },
            "required": ["action", "plan"],
        },
        handler=event_guest,
    )
    registry.register(
        name="event_vendor",
        description="Add a vendor quote to an event plan, compare quotes in one category (cheapest, dearest, spread, average), or list a plan's vendors. Quotes are numbers the owner types in — never payments, never a price feed. Use when the user records a vendor, compares caterers or venues, or asks what vendors cost.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "'add', 'compare' or 'list'"},
                "plan": {"type": "string",
                         "description": "The plan's name"},
                "category": {"type": "string",
                             "description": "The vendor category, e.g. 'venue' or 'catering'"},
                "name": {"type": "string",
                         "description": "The vendor's name"},
                "quote": {"type": "number",
                          "description": "The vendor's quoted price"},
            },
            "required": ["action", "plan"],
        },
        handler=event_vendor,
    )
    registry.register(
        name="event_budget",
        description="Estimate an event's cost (per-guest tier rate times guest count, plus vendor quotes) or summarise the budget picture against the plan's total budget. Read-only — pure computation over the stored plan, nothing is written or fetched. Tier rates are rough planning defaults; pass per_guest with the venue's actual figure for a real number.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "'estimate' or 'summary'"},
                "plan": {"type": "string",
                         "description": "The plan's name"},
                "tier": {"type": "string",
                         "description": "Budget tier to estimate with: modest, standard, premium or luxury (default: the plan's tier)"},
                "guests": {"type": "integer",
                           "description": "Guest count to estimate for (default: the plan's expected count, else the added guests)"},
                "per_guest": {"type": "number",
                              "description": "Per-guest rate to use instead of the tier default"},
            },
            "required": ["action", "plan"],
        },
        handler=event_budget,
    )
    registry.register(
        name="event_milestone",
        description="Add a dated milestone to an event plan ('book the venue by 1 June'), mark one done, or list a plan's milestones. With remind=true and a future due date, a scheduler reminder fires at 09:00 on the due date — the reminder is opt-in, never silent. Use when the user tracks what still needs doing before the event.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "'add', 'complete' or 'list'"},
                "plan": {"type": "string",
                         "description": "The plan's name"},
                "title": {"type": "string",
                          "description": "The milestone, e.g. 'Book the venue'"},
                "due": {"type": "string",
                        "description": "The due date, YYYY-MM-DD"},
                "remind": {"type": "boolean",
                           "description": "True to set a scheduler reminder for 09:00 on the due date"},
            },
            "required": ["action", "plan"],
        },
        handler=event_milestone,
    )
    registry.register(
        name="event_schedule",
        description="Add a timed item to an event plan's day-of run-of-show ('18:00 first dance') or list it in time order. A run-of-show item is a time on the day itself, not a deadline before it. Use when the user plans the order of the day.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "'add' or 'list'"},
                "plan": {"type": "string",
                         "description": "The plan's name"},
                "time": {"type": "string",
                         "description": "The time of day, HH:MM, e.g. '18:00'"},
                "title": {"type": "string",
                          "description": "The item, e.g. 'First dance'"},
            },
            "required": ["action", "plan"],
        },
        handler=event_schedule,
    )
