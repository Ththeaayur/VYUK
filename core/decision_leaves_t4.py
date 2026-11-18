"""Triage Wire Build 4 — the two decision-gated WIRE leaves, as
live tools, without modifying the leaves themselves (edition
precedent: leaves take no cross-module imports; this coordinator
imports them, the same split as ``core/study_flow_t1.py`` and
``core/creative_dev_social_t2.py``).

DECISION 1 — Q10 single store (owner ruling, supersedes the old
J9-only decision recorded here and in
``core/brain/goal_client_supply.py``): the loop's SINGLE playbook
store is core/playbook_store.py, holding BOTH the J9 goal entries
and the finance bullets (formerly ``core/fl_playbook_v574.py``,
FL-4-1 ACE bullets) — one file, two namespaces. The finance
tools here still use bullets ONLY: a finance bullet is never
handed to ``planner.run_goal``'s playbook lookup, so one goal
can never pull two playbooks' steps. The supply module
(``goal_client_supply.shared_playbook`` / ``playbook_store``)
is the single store — not the planner, and not this file.
This coordinator therefore exposes finance bullets as tools
(add / list / vote / delete / curate) and never hands a finance
bullet to the loop.

DECISION 2 — so_ledger (interim, owner-vetoable, recorded here):
Wave 6 vault promises are not built yet, so the social ledger
(``core/social_occasions_ledger.py``) is the SINGLE store for
occasions / promises / gifts / debts + respectful nudges UNTIL
the vault arrives. When the vault arrives, the ledger either
migrates into it (a migration tool reading this ledger's JSON
and writing vault promises — NOT built here, because the vault
does not exist yet) or is superseded by it; until then, no
second vault-shaped store is created. The ledger store is its
own JSON file, separate from the frozen memory schema — the
leaf never writes to the vault and never mutates the memory
DB, and neither does this coordinator.

Finance-bullet store (Q10 single store): bullets live in the
single store file ``data/playbooks.json`` (versioned JSON,
atomic saves, corrupt/missing file fails to an EMPTY store
with a note and is never overwritten on a failed load),
alongside the goal entries — one file, two namespaces. An
older install's ``data/finance_playbook.json`` is imported
on load when the single file has no bullets, and left
untouched. Bullet identity is its text (deduped by normalised
text); the numeric ids the tools return are positional in
the stored list, so callers re-list before deleting, the
same as any list tool. The store's ``list_bullets`` sorts
best-first; this coordinator lists in stored order so ids
stay stable across reloads.

Nudges are suggestions, never messages: ``social_nudge_check``
returns the leaf's fire/reason decision and nothing is sent, by
any tool here. There is no send function in the leaf and none
is added here.

Honesty limits, stated not hidden:

* No occasion, promise, gift, or debt is ever invented: every
  entry comes from values the caller states, and the leaf
  refuses empty person/counterparty/text, bad dates, bad
  kinds, and bad directions instead of filling anything in.
* The ledger leaf has no delete function of its own, so
  ``social_entry_delete`` is coordinator-level: it removes one
  entry by kind + id from the ledger's own JSON file, atomically,
  and is confirm-gated. It never touches nudges (cadence
  records are snoozed/paused, not deleted) and never touches
  the memory DB.
* Field proof on HIS machine after the BB101 quiz (a real
  finance playbook entry, a real occasion/debt recorded and
  nudged) is owed and is not claimed here.
* Surface: tools only. The Reminders page is session-only
  reminders (``gui/window_pages.py``: they live only in the
  session), so it is not a natural row for a persistent ledger;
  no page row was added. Palette + skills catalogue derive
  from live tool declarations, so these tools appear there
  automatically, as in Triage Wire Builds 1-3.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

def _ok(payload):
    return json.dumps({"status": "ok", **payload})


def _err(message):
    return json.dumps({"status": "error", "message": str(message)})


def _today_str():
    return date.today().isoformat()


def _normalise(text):
    return re.sub(r"\s+", " ", text.strip().casefold())


# ---------------------------------------------------------------------------
# Single-store seam (tests substitute this, as in study_flow).
# Finance bullets live in the Q10 single store (core/playbook_store.py),
# one file with the goal entries; the old separate finance file is
# imported on load when the single file has no bullets.
# ---------------------------------------------------------------------------

_LEDGER_KINDS = ("occasions", "promises", "gifts", "debts")

def _single_store_path() -> Path:
    """The single-store file (test seam: tests substitute this)."""
    from .memory import playbook_store as _ps

    return _ps.default_playbook_path()


def _load_store():
    """A PlaybookStore loaded from the single-store file.

    Fresh on every call, like the old per-call finance load:
    finance tools never hold bullets across calls, they read
    the file, mutate, and save.
    """
    from .memory import playbook_store as _ps

    book = _ps.PlaybookStore(_single_store_path())
    book.load()
    return book


def _seed_store(bullets):
    """A transient store seeded from stored bullets, in stored order.

    Seeded ids therefore equal positional ids (1-based index
    into the stored list), which is what makes delete-by-id
    from a fresh load correct. The transient store never saves.
    """
    from .memory import playbook_store as _ps

    book = _ps.PlaybookStore()
    for entry in bullets:
        bullet_id = book.add_bullet(entry["text"], source=entry["source"])
        for _ in range(entry["helpful"]):
            book.helpful(bullet_id)
        for _ in range(entry["harmful"]):
            book.harmful(bullet_id)
    return book


def _fl_listed(bullets):
    return [{"id": i + 1, "text": b["text"], "source": b["source"],
             "helpful": b["helpful"], "harmful": b["harmful"],
             "score": b["helpful"] - b["harmful"]}
            for i, b in enumerate(bullets)]


# ---------------------------------------------------------------------------
# Finance playbook tools (single store: playbook_store finance namespace).
# ---------------------------------------------------------------------------

def finance_playbook_add(text, source="user"):
    """Add a finance bullet; duplicates merge (leaf dedupe)."""
    store = _load_store()
    bullets = store.bullets
    book = _seed_store(bullets)
    bullet_id = book.add_bullet(text, source=source or "user")
    if bullet_id <= len(bullets):
        return {"bullet_id": bullet_id, "duplicate": True,
                "bullets": _fl_listed(bullets)}
    bullets.append({"text": str(text).strip(), "source": source or "user",
                    "helpful": 0, "harmful": 0})
    saved = store.save()
    return {"bullet_id": len(bullets), "duplicate": False,
            "persisted": saved["saved"], "bullets": _fl_listed(bullets)}


def finance_playbook_list():
    """List finance bullets in stored order, with counters."""
    store = _load_store()
    return {"notes": store.last_load_notes,
            "bullets": _fl_listed(store.bullets)}


def finance_playbook_vote(bullet_id, direction):
    """Vote a bullet helpful or harmful (counter only)."""
    if direction not in ("helpful", "harmful"):
        raise ValueError("direction must be helpful/harmful")
    store = _load_store()
    bullets = store.bullets
    index = int(bullet_id) - 1
    if index < 0 or index >= len(bullets):
        raise ValueError(f"no finance bullet {bullet_id!r}")
    book = _seed_store(bullets)
    if direction == "helpful":
        book.helpful(int(bullet_id))
    else:
        book.harmful(int(bullet_id))
    # Map counters back by text (seeded ids are positional).
    by_text = {b["text"]: b for b in book.list_bullets()}
    for entry in bullets:
        leaf = by_text.get(entry["text"])
        if leaf is not None:
            entry["helpful"] = leaf["helpful"]
            entry["harmful"] = leaf["harmful"]
    saved = store.save()
    return {"bullet_id": int(bullet_id), "direction": direction,
            "persisted": saved["saved"], "bullets": _fl_listed(bullets)}


def finance_playbook_delete(bullet_id):
    """Delete a finance bullet by positional id (confirm-gated)."""
    store = _load_store()
    bullets = store.bullets
    index = int(bullet_id) - 1
    if index < 0 or index >= len(bullets):
        raise ValueError(f"no finance bullet {bullet_id!r}")
    removed = bullets.pop(index)
    saved = store.save()
    return {"deleted": removed, "persisted": saved["saved"],
            "bullets": _fl_listed(bullets)}


def finance_playbook_curate(traces_json):
    """Curate bullets from CALLER-SUPPLIED frozen traces only.

    Traces are [{"text", "outcome": helpful|harmful|neutral}];
    the leaf ignores neutral and malformed traces. Nothing is
    captured, watched, or read from any trace store here."""
    try:
        traces = json.loads(traces_json) if isinstance(traces_json, str) else traces_json
    except ValueError as exc:
        raise ValueError(f"traces_json is not valid JSON: {exc}")
    if not isinstance(traces, list):
        raise ValueError("traces_json must be a JSON list of traces")
    store = _load_store()
    bullets = store.bullets
    book = _seed_store(bullets)
    touched = book.curate_from_traces(traces)
    by_text = {b["text"]: b for b in book.list_bullets()}
    touched_texts = []
    for bullet_id in touched:
        leaf = next((b for b in book.list_bullets() if b["id"] == bullet_id), None)
        if leaf is None:
            continue
        touched_texts.append(leaf["text"])
        existing = next((e for e in bullets
                         if _normalise(e["text"]) == _normalise(leaf["text"])), None)
        if existing is not None:
            existing["helpful"] = leaf["helpful"]
            existing["harmful"] = leaf["harmful"]
        else:
            bullets.append({"text": leaf["text"], "source": "trace",
                            "helpful": leaf["helpful"],
                            "harmful": leaf["harmful"]})
    saved = store.save()
    return {"touched": touched_texts, "persisted": saved["saved"],
            "bullets": _fl_listed(bullets)}


# ---------------------------------------------------------------------------
# Social ledger tools (leaf: social_occasions_ledger). Store seam: tests
# substitute this, as in study_flow; production uses the leaf's
# own default store file, separate from the memory DB.
# ---------------------------------------------------------------------------

def _ledger_store_path():
    from . import social_occasions_ledger as _ledger

    return _ledger.default_store_path()


def social_occasion_add(person, occ_type, month, day, year=None,
                        leap_policy="feb28", lead_times_json="[]", note=""):
    from . import social_occasions_ledger as _ledger

    try:
        lead_times = json.loads(lead_times_json) if lead_times_json else []
    except ValueError as exc:
        raise ValueError(f"lead_times_json is not valid JSON: {exc}")
    return _ledger.add_occasion(_ledger_store_path(), person, occ_type,
                                int(month), int(day), year=year,
                                leap_policy=leap_policy,
                                lead_times=lead_times, note=note or "")


def social_occasion_upcoming(today="", horizon_days=60):
    from . import social_occasions_ledger as _ledger

    return _ledger.upcoming_occasions(_ledger_store_path(),
                                      today or _today_str(),
                                      horizon_days=int(horizon_days))


def social_promise_add(counterparty, text, due="", wait="", until="",
                       depends_json="[]", recur_days=None, party="me"):
    from . import social_occasions_ledger as _ledger

    try:
        depends = json.loads(depends_json) if depends_json else []
    except ValueError as exc:
        raise ValueError(f"depends_json is not valid JSON: {exc}")
    return _ledger.add_promise(_ledger_store_path(), counterparty, text,
                               due=due or "", wait=wait or "",
                               until=until or "", depends=depends,
                               recur_days=recur_days, party=party)


def social_promise_list(today=""):
    from . import social_occasions_ledger as _ledger

    return _ledger.visible_promises(_ledger_store_path(),
                                    today or _today_str())


def social_promise_complete(promise_id, today=""):
    from . import social_occasions_ledger as _ledger

    return _ledger.complete_promise(_ledger_store_path(), promise_id,
                                    today or _today_str())


def social_gift_add(person, kind, description, date_text="", occasion=""):
    from . import social_occasions_ledger as _ledger

    return _ledger.add_gift(_ledger_store_path(), person, kind,
                            description, date_text=date_text or "",
                            occasion=occasion or "")


def social_debt_add(counterparty, amount_minor, currency, direction,
                    loaned_at=""):
    from . import social_occasions_ledger as _ledger

    return _ledger.add_debt(_ledger_store_path(), counterparty,
                            int(amount_minor), currency, direction,
                            loaned_at=loaned_at or "")


def social_debt_settle(debt_id, today=""):
    from . import social_occasions_ledger as _ledger

    return _ledger.settle_debt(_ledger_store_path(), debt_id,
                               today or _today_str())


def social_balances():
    from . import social_occasions_ledger as _ledger

    return _ledger.open_balances(_ledger_store_path())


def social_stay_in_touch(person, cadence_days, last_contact_logged=""):
    from . import social_occasions_ledger as _ledger

    return _ledger.set_stay_in_touch(_ledger_store_path(), person,
                                     int(cadence_days),
                                     last_contact_logged=last_contact_logged or "")


def social_nudge_check(person, today="", quiet_hours=False):
    """Nudge decision only — a suggestion, never a sent message."""
    from . import social_occasions_ledger as _ledger

    decision = _ledger.nudge_decision(_ledger_store_path(), person,
                                      today or _today_str(),
                                      quiet_hours=bool(quiet_hours))
    return {**decision, "suggestion_only": True, "sends_nothing": True}


def social_nudge_snooze(person, days, today=""):
    from . import social_occasions_ledger as _ledger

    return _ledger.snooze_person(_ledger_store_path(), person, int(days),
                                 today or _today_str())


def social_nudge_pause(person, paused=True):
    from . import social_occasions_ledger as _ledger

    return _ledger.pause_person(_ledger_store_path(), person, bool(paused))


def social_entry_delete(kind, entry_id):
    """Delete one ledger entry by kind + id (confirm-gated).

    The leaf has no delete of its own, so this is
    coordinator-level: it removes the entry from the ledger's
    own JSON file, atomically. Nudges are not deletable here
    (snooze/pause instead); the memory DB is never touched."""
    if kind not in _LEDGER_KINDS:
        raise ValueError(f"kind must be one of {_LEDGER_KINDS}, got {kind!r}")
    path = _ledger_store_path()
    if not os.path.exists(path):
        raise ValueError(f"no ledger entry {entry_id!r} in {kind}")
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    entries = data.get(kind, [])
    kept = [e for e in entries if e.get("id") != entry_id]
    if len(kept) == len(entries):
        raise ValueError(f"no ledger entry {entry_id!r} in {kind}")
    data[kind] = kept
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)
    return {"deleted_kind": kind, "deleted_id": entry_id}


# ---------------------------------------------------------------------------
# Tool handlers (JSON strings, as in triage3; handlers never raise).
# ---------------------------------------------------------------------------

def _handler(fn, **kwargs):
    try:
        return _ok({"result": fn(**kwargs)})
    except Exception as exc:  # noqa: BLE001 — handlers never raise
        return _err(exc)


def finance_playbook_add_handler(text, source="user"):
    return _handler(finance_playbook_add, text=text, source=source)


def finance_playbook_list_handler():
    return _handler(finance_playbook_list)


def finance_playbook_vote_handler(bullet_id, direction):
    return _handler(finance_playbook_vote, bullet_id=bullet_id,
                    direction=direction)


def finance_playbook_delete_handler(bullet_id):
    return _handler(finance_playbook_delete, bullet_id=bullet_id)


def finance_playbook_curate_handler(traces_json):
    return _handler(finance_playbook_curate, traces_json=traces_json)


def social_occasion_add_handler(person, occ_type, month, day, year=None,
                                leap_policy="feb28", lead_times_json="[]",
                                note=""):
    return _handler(social_occasion_add, person=person, occ_type=occ_type,
                    month=month, day=day, year=year,
                    leap_policy=leap_policy, lead_times_json=lead_times_json,
                    note=note)


def social_occasion_upcoming_handler(today="", horizon_days=60):
    return _handler(social_occasion_upcoming, today=today,
                    horizon_days=horizon_days)


def social_promise_add_handler(counterparty, text, due="", wait="", until="",
                               depends_json="[]", recur_days=None, party="me"):
    return _handler(social_promise_add, counterparty=counterparty, text=text,
                    due=due, wait=wait, until=until,
                    depends_json=depends_json, recur_days=recur_days,
                    party=party)


def social_promise_list_handler(today=""):
    return _handler(social_promise_list, today=today)


def social_promise_complete_handler(promise_id, today=""):
    return _handler(social_promise_complete, promise_id=promise_id, today=today)


def social_gift_add_handler(person, kind, description, date_text="",
                            occasion=""):
    return _handler(social_gift_add, person=person, kind=kind,
                    description=description, date_text=date_text,
                    occasion=occasion)


def social_debt_add_handler(counterparty, amount_minor, currency, direction,
                            loaned_at=""):
    return _handler(social_debt_add, counterparty=counterparty,
                    amount_minor=amount_minor, currency=currency,
                    direction=direction, loaned_at=loaned_at)


def social_debt_settle_handler(debt_id, today=""):
    return _handler(social_debt_settle, debt_id=debt_id, today=today)


def social_balances_handler():
    return _handler(social_balances)


def social_stay_in_touch_handler(person, cadence_days,
                                 last_contact_logged=""):
    return _handler(social_stay_in_touch, person=person,
                    cadence_days=cadence_days,
                    last_contact_logged=last_contact_logged)


def social_nudge_check_handler(person, today="", quiet_hours=False):
    return _handler(social_nudge_check, person=person, today=today,
                    quiet_hours=quiet_hours)


def social_nudge_snooze_handler(person, days, today=""):
    return _handler(social_nudge_snooze, person=person, days=days, today=today)


def social_nudge_pause_handler(person, paused=True):
    return _handler(social_nudge_pause, person=person, paused=paused)


def social_entry_delete_handler(kind, entry_id):
    return _handler(social_entry_delete, kind=kind, entry_id=entry_id)


# ---------------------------------------------------------------------------
# Registration (the module owns its catalogue, as t1/t2/t3 do).
# ---------------------------------------------------------------------------

def register_all(registry) -> None:
    """Register the 19 Triage Wire Build 4 tools.

    Finance playbook (finance-tool only, never the loop lookup):
    add, list, vote (app-state), delete (confirm-gated),
    curate (app-state). Social ledger (interim single store
    until the Wave 6 vault): occasion add/upcoming, promise
    add/list/complete, gift add, debt add/settle, balances,
    stay-in-touch, nudge check (read-only, suggestion only),
    nudge snooze/pause (app-state), entry delete
    (confirm-gated). No tool sends a message; no tool touches
    the memory DB or the vault.
    """
    registry.register(
        name="finance_playbook_add",
        description="Add a bullet to the local finance playbook (helpful/harmful counters, deletable, local-only, never exported). Duplicates merge by text. This is a finance-tool playbook only — it is never consulted by the loop's playbook lookup, whose single store is J9. Use when the user wants a finance lesson or rule remembered.",
        parameters={"type": "object", "properties": {
            "text": {"type": "string", "description": "Bullet text"},
            "source": {"type": "string", "description": "Bullet source (default user)"},
        }, "required": ["text"]},
        handler=finance_playbook_add_handler,
    )
    registry.register(
        name="finance_playbook_list",
        description="List the local finance playbook bullets in stored order with helpful/harmful counters and scores. Read-only. Use when the user wants to see their finance playbook.",
        parameters={"type": "object", "properties": {}},
        handler=finance_playbook_list_handler,
    )
    registry.register(
        name="finance_playbook_vote",
        description="Vote a finance playbook bullet helpful or harmful (counter only, no text change). Use when the user says a finance rule helped or hurt.",
        parameters={"type": "object", "properties": {
            "bullet_id": {"type": "integer", "description": "Positional bullet id from finance_playbook_list"},
            "direction": {"type": "string", "enum": ["helpful", "harmful"], "description": "helpful or harmful: whether the bullet helped or hurt"},
        }, "required": ["bullet_id", "direction"]},
        handler=finance_playbook_vote_handler,
    )
    registry.register(
        name="finance_playbook_delete",
        description="Delete a finance playbook bullet by id. The deletion asks for confirmation first. Use when the user wants a finance bullet removed.",
        parameters={"type": "object", "properties": {
            "bullet_id": {"type": "integer", "description": "Positional bullet id from finance_playbook_list"},
        }, "required": ["bullet_id"]},
        handler=finance_playbook_delete_handler,
    )
    registry.register(
        name="finance_playbook_curate",
        description="Curate finance playbook bullets from caller-supplied frozen traces only ([{text, outcome: helpful|harmful|neutral}]); neutral and malformed traces are ignored. Nothing is captured or read from any trace store. Use when the user supplies past traces to learn finance rules from.",
        parameters={"type": "object", "properties": {
            "traces_json": {"type": "string", "description": "JSON list of {text, outcome} traces"},
        }, "required": ["traces_json"]},
        handler=finance_playbook_curate_handler,
    )
    registry.register(
        name="social_occasion_add",
        description="Add an occasion (birthday, anniversary, or custom) for a person the user names, with the date parts the user supplies. Nothing is inferred from mail, messages, calendars, or chats. Use when the user wants an occasion remembered.",
        parameters={"type": "object", "properties": {
            "person": {"type": "string", "description": "The person the occasion is for"},
            "occ_type": {"type": "string", "enum": ["birthday", "anniversary", "custom"], "description": "birthday, anniversary, or custom"},
            "month": {"type": "integer", "description": "Month number of the occasion"}, "day": {"type": "integer", "description": "Day of the month of the occasion"},
            "year": {"type": "integer", "description": "Optional; omit when the year is unknown"},
            "leap_policy": {"type": "string", "description": "feb28 by default"},
            "lead_times_json": {"type": "string", "description": "JSON list of days-before to surface, e.g. [30, 7, 0]"},
            "note": {"type": "string", "description": "Optional note about the occasion"},
        }, "required": ["person", "occ_type", "month", "day"]},
        handler=social_occasion_add_handler,
    )
    registry.register(
        name="social_occasion_upcoming",
        description="List occasions whose next occurrence falls within the horizon, with next date, days away, and whether a lead time fired. Read-only. Use when the user asks what occasions are coming up.",
        parameters={"type": "object", "properties": {
            "today": {"type": "string", "description": "YYYY-MM-DD; omit for today"},
            "horizon_days": {"type": "integer", "description": "Default 60"},
        }},
        handler=social_occasion_upcoming_handler,
    )
    registry.register(
        name="social_promise_add",
        description="Add a promise with counterparty, text, and optional due/wait/until/depends/recur/party (me/them/both). Only values the user states or confirms. Use when the user makes a promise to someone.",
        parameters={"type": "object", "properties": {
            "counterparty": {"type": "string", "description": "The person the promise is made to or with"}, "text": {"type": "string", "description": "The promise itself, in the user's words"},
            "due": {"type": "string", "description": "YYYY-MM-DD or empty"},
            "wait": {"type": "string", "description": "YYYY-MM-DD or empty"},
            "until": {"type": "string", "description": "YYYY-MM-DD or empty"},
            "depends_json": {"type": "string", "description": "JSON list of promise ids this depends on"},
            "recur_days": {"type": "integer", "description": "Optional recurrence in days"},
            "party": {"type": "string", "enum": ["me", "them", "both"], "description": "Who owes the promise: me, them, or both"},
        }, "required": ["counterparty", "text"]},
        handler=social_promise_add_handler,
    )
    registry.register(
        name="social_promise_list",
        description="List promises visible today (wait-future hidden, until-past excluded). Read-only. Use when the user asks what promises are open.",
        parameters={"type": "object", "properties": {
            "today": {"type": "string", "description": "YYYY-MM-DD; omit for today"},
        }},
        handler=social_promise_list_handler,
    )
    registry.register(
        name="social_promise_complete",
        description="Complete a promise; a recurring promise spawns the next child instance, and the completed instance's history is never mutated. Use when the user says a promise is done.",
        parameters={"type": "object", "properties": {
            "promise_id": {"type": "string", "description": "Id of the promise to complete"},
            "today": {"type": "string", "description": "YYYY-MM-DD; omit for today"},
        }, "required": ["promise_id"]},
        handler=social_promise_complete_handler,
    )
    registry.register(
        name="social_gift_add",
        description="Add a gift record (idea, given, or received) for a person, with a description. Only values the user states. Use when the user wants a gift idea or gift remembered.",
        parameters={"type": "object", "properties": {
            "person": {"type": "string", "description": "The person the gift is for"},
            "kind": {"type": "string", "enum": ["idea", "given", "received"], "description": "idea, given, or received"},
            "description": {"type": "string", "description": "Description of the gift"},
            "date_text": {"type": "string", "description": "YYYY-MM-DD or empty"},
            "occasion": {"type": "string", "description": "Occasion the gift belongs to, if any"},
        }, "required": ["person", "kind", "description"]},
        handler=social_gift_add_handler,
    )
    registry.register(
        name="social_debt_add",
        description="Add a debt in integer minor units (paise/cents, never floats), direction i_owe or they_owe. Only values the user states; no debt is inferred from chats or payments. Use when the user records money owed.",
        parameters={"type": "object", "properties": {
            "counterparty": {"type": "string", "description": "The person money is owed to or by"},
            "amount_minor": {"type": "integer", "description": "Amount in integer minor units (paise/cents, never floats)"},
            "currency": {"type": "string", "description": "Currency of the debt"},
            "direction": {"type": "string", "enum": ["i_owe", "they_owe"], "description": "i_owe or they_owe"},
            "loaned_at": {"type": "string", "description": "YYYY-MM-DD or empty"},
        }, "required": ["counterparty", "amount_minor", "currency", "direction"]},
        handler=social_debt_add_handler,
    )
    registry.register(
        name="social_debt_settle",
        description="Settle a debt by id (records settled_at; the entry is kept, not erased). Use when the user says a debt is settled.",
        parameters={"type": "object", "properties": {
            "debt_id": {"type": "string", "description": "Id of the debt to settle"},
            "today": {"type": "string", "description": "YYYY-MM-DD; omit for today"},
        }, "required": ["debt_id"]},
        handler=social_debt_settle_handler,
    )
    registry.register(
        name="social_balances",
        description="Net open balance per counterparty per currency, in minor units (positive = they owe him, negative = he owes them). Read-only, from the ledger he keeps. Use when the user asks who owes what.",
        parameters={"type": "object", "properties": {}},
        handler=social_balances_handler,
    )
    registry.register(
        name="social_stay_in_touch",
        description="Set a stay-in-touch cadence the user chooses, for a person the user names, with an optional last-contact date the user logged. Use when the user wants to be nudged to contact someone regularly.",
        parameters={"type": "object", "properties": {
            "person": {"type": "string", "description": "The person to stay in touch with"},
            "cadence_days": {"type": "integer", "description": "Days between stay-in-touch nudges"},
            "last_contact_logged": {"type": "string", "description": "YYYY-MM-DD or empty"},
        }, "required": ["person", "cadence_days"]},
        handler=social_stay_in_touch_handler,
    )
    registry.register(
        name="social_nudge_check",
        description="Check whether a respectful nudge should fire for a person: only on a cadence the user set, for a person the user named, with its reason shown. The result is a suggestion only — it never drafts, sends, or auto-messages anything. Read-only. Use when the user asks whether to nudge someone.",
        parameters={"type": "object", "properties": {
            "person": {"description": "The person to check a nudge for", "type": "string"},
            "today": {"type": "string", "description": "YYYY-MM-DD; omit for today"},
            "quiet_hours": {"description": "True when quiet hours apply, so the nudge does not fire", "type": "boolean"},
        }, "required": ["person"]},
        handler=social_nudge_check_handler,
    )
    registry.register(
        name="social_nudge_snooze",
        description="Snooze a person's stay-in-touch nudges for a number of days, with no penalty. Use when the user wants to pause nudges for someone temporarily.",
        parameters={"type": "object", "properties": {
            "person": {"description": "The person whose nudges are snoozed", "type": "string"}, "days": {"description": "Number of days to snooze nudges for", "type": "integer"},
            "today": {"type": "string", "description": "YYYY-MM-DD; omit for today"},
        }, "required": ["person", "days"]},
        handler=social_nudge_snooze_handler,
    )
    registry.register(
        name="social_nudge_pause",
        description="Pause or resume a person's stay-in-touch nudges (no penalty, no streak loss). Use when the user wants nudges for someone paused or resumed.",
        parameters={"type": "object", "properties": {
            "person": {"description": "The person whose nudges are paused or resumed", "type": "string"},
            "paused": {"type": "boolean", "description": "Default true"},
        }, "required": ["person"]},
        handler=social_nudge_pause_handler,
    )
    registry.register(
        name="social_entry_delete",
        description="Delete one ledger entry by kind (occasions, promises, gifts, debts) and id. The deletion asks for confirmation first. Nudges are not deletable here (snooze or pause instead). Use when the user wants a ledger entry removed.",
        parameters={"type": "object", "properties": {
            "kind": {"description": "occasions, promises, gifts, or debts", "type": "string", "enum": ["occasions", "promises", "gifts", "debts"]},
            "entry_id": {"description": "Id of the ledger entry to delete", "type": "string"},
        }, "required": ["kind", "entry_id"]},
        handler=social_entry_delete_handler,
    )


__all__ = [
    "finance_playbook_add", "finance_playbook_list",
    "finance_playbook_vote", "finance_playbook_delete",
    "finance_playbook_curate",
    "social_occasion_add", "social_occasion_upcoming",
    "social_promise_add", "social_promise_list",
    "social_promise_complete", "social_gift_add",
    "social_debt_add", "social_debt_settle", "social_balances",
    "social_stay_in_touch", "social_nudge_check",
    "social_nudge_snooze", "social_nudge_pause",
    "social_entry_delete", "register_all",
]
