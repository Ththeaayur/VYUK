import datetime
import json
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
    _store,
)


# ---------------------------------------------------------------------------
# CONTACT CARD — labelled details kept per person
# ---------------------------------------------------------------------------

_CONTACT_CATEGORY = "contact"


def _load_contacts() -> tuple[list, int]:
    """(entries, broken_count): {person, detail} dicts."""
    entries, broken = _load(_CONTACT_CATEGORY, {
        "person": _nonempty, "detail": _nonempty})
    for entry in entries:
        entry["person"] = str(entry.get("person") or "")
        entry["detail"] = str(entry.get("detail") or "")
    return entries, broken


def list_contacts() -> list[dict]:
    """Every stored person, grouped — NOT a tool.

    Powers the Contacts list view (Stage 0 features batch 1):
    [{"person", "details": [str, ...]}] sorted by person name,
    details sorted. ``contact_card`` saves/shows one person; this
    is the list of all of them. Raises like ``_load_contacts``
    when the store can't be read — callers say so plainly.
    """
    entries, _broken = _load_contacts()
    grouped: dict[str, dict] = {}
    for entry in entries:
        person = (entry.get("person") or "").strip()
        detail = (entry.get("detail") or "").strip()
        if not person or not detail:
            continue
        key = person.lower()
        if key not in grouped:
            grouped[key] = {"person": person, "details": []}
        if detail not in grouped[key]["details"]:
            grouped[key]["details"].append(detail)
    out = list(grouped.values())
    for person in out:
        person["details"].sort(key=str.lower)
    out.sort(key=lambda p: p["person"].lower())
    return out


def contact_card(action: str, person: str = "",
                 detail: str = "") -> str:
    """Save one labelled detail for a person ('phone: …',
    'email: …', an address, a note), or show everything stored
    for them."""
    action = str(action or "").strip().lower()
    if action not in ("save", "show"):
        return ("Contact card actions are 'save' and 'show' — "
                "e.g. contact_card with action 'save', a "
                "person and a labelled detail like 'phone: "
                "+91 …'.")
    clean_person = _clean(person)
    if not clean_person:
        return "That needs the person's name."
    if len(clean_person) > _MAX_NAME_CHARS:
        return (f"That name is {len(clean_person)} characters "
                f"— names are capped at {_MAX_NAME_CHARS}.")
    if action == "show":
        try:
            entries, broken = _load_contacts()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the contact cards: {e}"
        mine = [e for e in entries
                if e["person"].lower() == clean_person.lower()]
        if not mine:
            base = (f"No contact details stored for "
                    f"'{clean_person}' yet. Save one with "
                    "contact_card, action 'save', the person "
                    "and a labelled detail.")
            if broken:
                base += " " + _broken_note(broken, "contact")
            return base
        mine.sort(key=lambda e: e["detail"].lower())
        lines = [f"Contact details for {mine[0]['person']} "
                 f"({len(mine)}):"]
        for entry in mine:
            lines.append(f"• {entry['detail']}")
        if broken:
            lines.append(_broken_note(broken, "contact"))
        return "\n".join(lines)
    # action == "save"
    clean_detail = _clean(detail)
    if not clean_detail:
        return ("Saving a contact detail needs the detail "
                "itself — label it as you save it, e.g. "
                "'phone: …' or 'email: …'.")
    if len(clean_detail) > _MAX_TEXT_CHARS:
        return (f"That detail is {len(clean_detail)} "
                f"characters — details are capped at "
                f"{_MAX_TEXT_CHARS}.")
    key = (clean_person.lower() + " | "
           + clean_detail.lower()[:_MAX_NAME_CHARS])
    payload = {"person": clean_person, "detail": clean_detail}
    try:
        existed = _save(_CONTACT_CATEGORY, key, payload)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't save the contact detail: {e}"
    return (f"{'Updated' if existed else 'Saved'} for "
            f"{clean_person}: {clean_detail}.")


# ---------------------------------------------------------------------------
# SERVICE SCHEDULE — recurring upkeep with computed due dates,
# plus MAINTENANCE DUE, the read across the schedule
# ---------------------------------------------------------------------------

_SERVICE_CATEGORY = "service"


def _load_services() -> tuple[list, int]:
    """(entries, broken_count): {item, last_done, every_months,
    next_due} dicts."""
    def date_ok(value) -> bool:
        return _parse_date(value) is not None

    entries, broken = _load(_SERVICE_CATEGORY, {
        "item": _nonempty, "next_due": date_ok})
    for entry in entries:
        entry["item"] = str(entry.get("item") or "")
        entry["last_done"] = str(entry.get("last_done") or "")
        entry["next_due"] = str(
            entry.get("next_due") or "").strip()
        try:
            entry["every_months"] = int(
                float(entry.get("every_months")))
        except (TypeError, ValueError):
            entry["every_months"] = 0
    return entries, broken


def service_schedule(action: str, item: str = "",
                     last_done: str = "", every_months=0) -> str:
    """Record when something was last serviced and how often
    it needs doing (the next due date is computed), or list
    the schedule, soonest due first."""
    action = str(action or "").strip().lower()
    if action not in ("save", "list"):
        return ("Service schedule actions are 'save' and "
                "'list' — e.g. service_schedule with action "
                "'save', the item, when it was last done and "
                "how many months between services.")
    if action == "list":
        try:
            entries, broken = _load_services()
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the service schedule: {e}"
        if not entries:
            base = ("No service schedules stored yet. Add "
                    "one with service_schedule, action "
                    "'save', the item, when it was last done "
                    "and the interval in months.")
            if broken:
                base += " " + _broken_note(broken, "schedule")
            return base
        today = datetime.date.today()
        entries.sort(key=lambda e: e["next_due"])
        lines = [f"Service schedule ({len(entries)}):"]
        for entry in entries:
            due = _parse_date(entry["next_due"])
            delta = (due - today).days
            if delta < 0:
                status = f"overdue ({_days_words(delta)})"
            elif delta == 0:
                status = "due today"
            else:
                status = f"due {_days_words(delta)}"
            line = (f"• {entry['item']} — next due "
                    f"{entry['next_due']} ({status})")
            details = []
            if entry["last_done"]:
                details.append(
                    f"last done {entry['last_done']}")
            if entry["every_months"]:
                details.append(
                    f"every {entry['every_months']} months")
            if details:
                line += " — " + ", ".join(details)
            lines.append(line)
        if broken:
            lines.append(_broken_note(broken, "schedule"))
        return "\n".join(lines)
    # action == "save"
    clean_item = _clean(item)
    if not clean_item:
        return "Saving a schedule needs the item's name."
    if len(clean_item) > _MAX_NAME_CHARS:
        return (f"That item name is {len(clean_item)} "
                f"characters — names are capped at "
                f"{_MAX_NAME_CHARS}.")
    done_text = str(last_done or "").strip()
    done_day = _parse_date(done_text)
    if done_day is None:
        return _bad_date_line(done_text)
    months = _coerce_int(every_months, 0, 0, 600)
    if months < 1:
        return ("The interval needs to be a whole number of "
                "months, at least 1 — e.g. 6 for twice a year. "
                "Nothing was saved.")
    due_day = _add_months(done_day, months)
    payload = {"item": clean_item,
               "last_done": done_day.isoformat(),
               "every_months": months,
               "next_due": due_day.isoformat()}
    try:
        existed = _save(_SERVICE_CATEGORY, clean_item.lower(),
                        payload)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't save the schedule: {e}"
    return (f"{'Updated' if existed else 'Saved'} the "
            f"schedule for '{clean_item}' — last done "
            f"{done_day.isoformat()}, every {months} months, "
            f"next due {due_day.isoformat()}.")


def maintenance_due() -> str:
    """What's overdue and what's due within 30 days across
    the service schedule, with due dates and by how much."""
    try:
        entries, broken = _load_services()
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read the service schedule: {e}"
    broken_line = ""
    if broken:
        broken_line = " " + _broken_note(broken, "schedule")
    if not entries:
        return ("No service schedules are stored yet — add "
                "one with service_schedule (the item, when it "
                "was last done, and how many months between "
                "services), and I'll watch the due dates."
                + broken_line)
    today = datetime.date.today()
    horizon = today + datetime.timedelta(days=30)
    enriched = [(entry, _parse_date(entry["next_due"]))
                for entry in entries]
    overdue = sorted([p for p in enriched if p[1] < today],
                     key=lambda p: p[1])
    soon = sorted([p for p in enriched
                   if today <= p[1] <= horizon],
                  key=lambda p: p[1])
    if not overdue and not soon:
        nxt_entry, nxt_day = min(enriched, key=lambda p: p[1])
        return (f"Nothing overdue and nothing due in the "
                f"next 30 days — {len(entries)} schedule"
                f"{'s' if len(entries) != 1 else ''} on file; "
                f"the next is '{nxt_entry['item']}' on "
                f"{nxt_day.isoformat()} "
                f"({_days_words((nxt_day - today).days)})."
                + broken_line)
    lines = ["Maintenance due:"]
    if overdue:
        lines.append("Overdue:")
        for entry, due in overdue:
            lines.append(
                f"• {entry['item']} — was due "
                f"{due.isoformat()} (overdue by "
                f"{(today - due).days} day"
                f"{'s' if (today - due).days != 1 else ''}); "
                f"last done {entry['last_done']}, every "
                f"{entry['every_months']} months.")
    if soon:
        lines.append("Due within 30 days:")
        for entry, due in soon:
            delta = (due - today).days
            when = ("due today" if delta == 0 else
                    f"due {_days_words(delta)}")
            lines.append(
                f"• {entry['item']} — {when} "
                f"({due.isoformat()}); last done "
                f"{entry['last_done']}, every "
                f"{entry['every_months']} months.")
    if broken:
        lines.append(_broken_note(broken, "schedule"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# EMERGENCY CARD — one consolidated card, stored locally only
# ---------------------------------------------------------------------------

_EMERGENCY_CATEGORY = "emergency"
_EMERGENCY_KEY = "card"
_EMERGENCY_FIELDS = (("name", "Name"),
                     ("blood_group", "Blood group"),
                     ("allergies", "Allergies"),
                     ("emergency_contact", "Emergency contact"),
                     ("note", "Note"))


def emergency_card(action: str, name: str = "",
                   blood_group: str = "", allergies: str = "",
                   emergency_contact: str = "",
                   note: str = "") -> str:
    """Set the one emergency card (name, blood group,
    allergies, emergency contact, note) or show it back
    verbatim."""
    action = str(action or "").strip().lower()
    if action not in ("set", "show"):
        return ("Emergency card actions are 'set' and "
                "'show' — e.g. emergency_card with action "
                "'set', a name, blood group, allergies and an "
                "emergency contact.")
    if action == "show":
        try:
            fact = _store().get_fact(_EMERGENCY_CATEGORY,
                                     _EMERGENCY_KEY)
        except Exception as e:  # noqa: BLE001
            return f"Couldn't read the emergency card: {e}"
        if fact is None:
            return ("Your emergency card isn't set yet. Set "
                    "it with emergency_card, action 'set' — "
                    "name, blood group, allergies, an "
                    "emergency contact and a note.")
        try:
            stored = json.loads(fact.get("value") or "")
            if not isinstance(stored, dict):
                raise ValueError
        except Exception:  # noqa: BLE001
            return "The stored emergency card couldn't be read."
        lines = ["Emergency card:"]
        for field, label in _EMERGENCY_FIELDS:
            value = str(stored.get(field) or "")
            if value:
                lines.append(f"• {label}: {value}")
        if len(lines) == 1:
            return ("Your emergency card is set but every "
                    "field on it is blank.")
        return "\n".join(lines)
    # action == "set"
    payload = {"name": _clean(name),
               "blood_group": _clean(blood_group),
               "allergies": _clean(allergies),
               "emergency_contact": _clean(emergency_contact),
               "note": _clean(note)}
    for field, label in _EMERGENCY_FIELDS:
        cap = (_MAX_NAME_CHARS if field in ("name", "blood_group")
               else _MAX_TEXT_CHARS)
        if len(payload[field]) > cap:
            return (f"The {label.lower()} is "
                    f"{len(payload[field])} characters — it's "
                    f"capped at {cap}. Nothing was saved.")
    if not any(payload.values()):
        return ("There's nothing to put on the card — give "
                "me at least a name, blood group, allergies, "
                "an emergency contact or a note.")
    try:
        _save(_EMERGENCY_CATEGORY, _EMERGENCY_KEY, payload)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't save the emergency card: {e}"
    filled = [label for field, label in _EMERGENCY_FIELDS
              if payload[field]]
    return ("Saved your emergency card ("
            + ", ".join(filled)
            + "). It is stored locally in VYUK's memory "
              "on this machine only — it isn't sent or synced "
              "anywhere.")
