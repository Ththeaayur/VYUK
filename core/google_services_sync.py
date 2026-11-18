"""Part of the core/google_services.py split (B25-style):
google_services_sync. The sync_updates tool (GAPS-7) — "what's new
in my mail and calendar since the last check", one incremental pass
over both connectors through the shared sync engine.

Before GAPS-7 there was no sync at all: each listing was one page
capped at 25 with no retry and no memory. This tool is the
engine's user-facing surface: it pages until exhausted, retries
transient failures, and remembers a watermark per source, so a
re-sync fetches only what's new:

- mail: Gmail `is:unread after:<since_date>` — since_date is the
  last sync's date minus one day of overlap (a message dated
  yesterday that arrives today is still caught), and message ids
  already reported are dropped from the re-fetch;
- calendar: events.list with updatedMin=<last_sync> over the
  upcoming 14 days — an event counts as new when its id is unseen
  or its `updated` stamp moved since the last sync.

The watermark advances ONLY on a complete sync AND a verified
state save (SyncState.put re-reads the file — the A1-store
pattern). A partial sync reports what did land, says the watermark
did not advance, and retries the same window next time — a partial
set is never silently presented as whole, and never moves the
watermark past items it did not fetch. A missing or corrupt state
file reads as empty: the next sync re-fetches everything and says
so (first sync), it never blocks the sync.

Free by design (app-state): it reads Google and writes only its
own watermark file under data/. It never sends, deletes or creates
anything outside VYUK.
"""

import datetime

from . import injection
from .connector_sync import (SyncState, describe_partial)
from .google_services_calendar import (
    _fetch_events_paged, _format_event)
from .google_services_gmail import _list_messages, _render_message
from .google_services_services import _service_or_failure

_GMAIL_KEY = "gmail_unread"
_CALENDAR_KEY = "calendar_upcoming"
_CALENDAR_WINDOW_DAYS = 14


def _display_since(iso: str) -> str:
    """A stored last-sync timestamp as "12 Nov 14:20" in local time,
    or "" when there is none / it won't parse."""
    if not iso:
        return ""
    try:
        when = datetime.datetime.fromisoformat(
            iso.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when.astimezone().strftime("%d %b %H:%M")


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def _sync_gmail(svc, state: SyncState, full: bool) -> str:
    """One incremental Gmail pass. Returns the mail section."""
    if full:
        state.reset(_GMAIL_KEY)
    saved = state.get(_GMAIL_KEY)
    since_date = (saved.get("since_date") or "").strip()
    seen_ids = set(saved.get("seen_ids") or [])
    since_display = _display_since(saved.get("last_sync") or "")
    query = "is:unread"
    if since_date:
        query += " after:%s" % since_date
    listing = _list_messages(svc, query, None)
    stubs = listing.get("messages") or []
    today = datetime.datetime.now().astimezone().date()
    new = [s for s in stubs if (s.get("id") or "") not in seen_ids]
    body_lines: list[str] = []
    failed = 0
    for stub in new:
        rendered = _render_message(svc, stub, today)
        if rendered:
            body_lines.extend(rendered)
        else:
            failed += 1
    if since_display:
        head = f"New mail since {since_display}: "
    else:
        head = "New mail (first sync): "
    if not listing.get("complete", True):
        # Partial: report what did land, do NOT advance the
        # watermark — the next sync retries the same window.
        lines = [head + f"{len(new)} new"
                 f"{describe_partial(listing)}."]
        if body_lines:
            lines.append(injection.wrap_untrusted(
                "email", "\n".join(body_lines)))
        lines.append("(The sync was incomplete, so the watermark did "
                     "not advance — the next sync retries the same "
                     "window.)")
        return "\n".join(lines)
    # Complete: advance the watermark — one day of overlap, so a
    # message dated yesterday that arrives today is still caught,
    # and the seen ids drop it from the re-fetch.
    now_iso = datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    new_since = (today - datetime.timedelta(days=1)).strftime(
        "%Y/%m/%d")
    ids = [(s.get("id") or "") for s in stubs]
    ok, err = state.put(_GMAIL_KEY, {
        "last_sync": now_iso,
        "since_date": new_since,
        "seen_ids": ids,
    })
    lines = [head + f"{len(new)} new."]
    if body_lines:
        lines.append(injection.wrap_untrusted(
            "email", "\n".join(body_lines)))
    if failed:
        lines.append(
            f"({failed} new message{'s' if failed != 1 else ''} "
            "couldn't be loaded.)")
    if not new and not failed:
        lines = [head.rstrip(": ") + " — nothing new."]
        lines[0] = lines[0].replace(
            "New mail since", "No new mail since").replace(
            "New mail (first sync)", "No new mail")
    if not ok:
        lines.append(
            f"(Couldn't save the sync state ({err}) — the next sync "
            "may repeat these.)")
    return "\n".join(lines)


def _sync_calendar(svc, state: SyncState, full: bool) -> str:
    """One incremental Calendar pass. Returns the calendar section."""
    if full:
        state.reset(_CALENDAR_KEY)
    saved = state.get(_CALENDAR_KEY)
    last_sync = (saved.get("last_sync") or "").strip()
    known = saved.get("known") or {}
    if not isinstance(known, dict):
        known = {}
    since_display = _display_since(last_sync)
    now = datetime.datetime.now().astimezone()
    end = now + datetime.timedelta(days=_CALENDAR_WINDOW_DAYS)
    items, report = _fetch_events_paged(
        svc, now, end, updated_min=last_sync or None)
    today = now.date()
    new = []
    for ev in items:
        eid = str(ev.get("id") or "")
        if not eid:
            continue
        updated = str(ev.get("updated") or "")
        if eid not in known or known.get(eid) != updated:
            new.append(ev)
    if since_display:
        head = f"New calendar events since {since_display}: "
    else:
        head = "New calendar events (first sync): "
    if not report.get("complete", True):
        # Partial: report what did land, do NOT advance the
        # watermark — the next sync retries the same window.
        lines = [head + f"{len(new)} new"
                 f"{report.get('partial') or ''}."]
        if new:
            lines.append(injection.wrap_untrusted(
                "calendar",
                "\n".join(_format_event(ev, today, True)
                          for ev in new)))
        lines.append("(The sync was incomplete, so the watermark did "
                     "not advance — the next sync retries the same "
                     "window.)")
        return "\n".join(lines)
    now_iso = datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    known_new = {str(ev.get("id") or ""): str(ev.get("updated") or "")
                 for ev in items if ev.get("id")}
    ok, err = state.put(_CALENDAR_KEY, {
        "last_sync": now_iso,
        "known": known_new,
    })
    if not new:
        line = head.rstrip(": ") + " — nothing new."
        line = line.replace("New calendar events since",
                            "No new calendar events since").replace(
            "New calendar events (first sync)",
            "No new calendar events")
        lines = [line]
    else:
        lines = [head + f"{len(new)} new."]
        lines.append(injection.wrap_untrusted(
            "calendar",
            "\n".join(_format_event(ev, today, True) for ev in new)))
    if not ok:
        lines.append(
            f"(Couldn't save the sync state ({err}) — the next sync "
            "may repeat these.)")
    return "\n".join(lines)


def sync_updates(sources: str = "both", full: bool = False) -> str:
    """What's new in the user's mail and calendar since the last
    sync (GAPS-7) — one incremental pass over both connectors
    through the shared sync engine: pages until exhausted, retries
    transient failures, and remembers a watermark per source so a
    re-sync fetches only what's new. ``sources`` is 'mail',
    'calendar' or 'both' (default 'both'); ``full`` re-syncs
    everything (the watermarks reset first) and reports it all as
    new. A source that isn't connected reports its own failure
    sentence and the other source still syncs."""
    sources = (sources or "both").strip().lower()
    if sources not in ("mail", "calendar", "both"):
        return ("Sync which — 'mail', 'calendar', or 'both'? "
                f"'{sources}' isn't one of those.")
    full = _coerce_bool(full)
    state = SyncState()
    sections: list[str] = []
    if sources in ("mail", "both"):
        svc, failure = _service_or_failure("gmail", "your mail")
        if svc is None:
            sections.append("Mail: " + failure)
        else:
            sections.append(_sync_gmail(svc, state, full))
    if sources in ("calendar", "both"):
        svc, failure = _service_or_failure("calendar", "your calendar")
        if svc is None:
            sections.append("Calendar: " + failure)
        else:
            sections.append(_sync_calendar(svc, state, full))
    return "\n".join(sections)
