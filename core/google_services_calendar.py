"""Part of the core/google_services.py split (B25): google_services_calendar. Move only — see core/google_services.py for the facade."""

import datetime
from .google_services_helpers import CALENDAR_EVENTS_SCOPE, _MONTHS, _WEEKDAYS, _short  # INTENTIONAL (B25): shared names
from .google_services_services import _calendar_service_or_none, _is_scope_error, _scope_missing_message, _service_or_failure, _write_service_or_failure  # INTENTIONAL (B25): service family
from . import injection  # INTENTIONAL (B25): module-level injection, as in the old module
from .connector_sync import (  # GAPS-7: the shared sync engine
    DEFAULT_PAGE_SIZE, MAX_LIST_ITEMS, describe_partial, paginate)


def _local_tz():
    return datetime.datetime.now().astimezone().tzinfo


def _parse_event_dt(value: str):
    try:
        when = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_local_tz())
    return when.astimezone(_local_tz())


def _event_start(event: dict):
    """Returns ("allday", date, None) or ("timed", start_dt, end_dt|None),
    or None when the event carries nothing parseable."""
    start = event.get("start") or {}
    end = event.get("end") or {}
    if start.get("date"):
        try:
            day = datetime.date.fromisoformat(start["date"])
        except Exception:  # noqa: BLE001
            return None
        return ("allday", day, None)
    if start.get("dateTime"):
        begin = _parse_event_dt(start["dateTime"])
        if begin is None:
            return None
        finish = _parse_event_dt(end["dateTime"]) if end.get("dateTime") else None
        return ("timed", begin, finish)
    return None


def _format_event(event: dict, today: datetime.date, with_date_prefix: bool) -> str:
    parsed = _event_start(event)
    title = (event.get("summary") or "(untitled event)").strip()
    location = (event.get("location") or "").strip()
    suffix = f" ({location})" if location else ""
    if parsed is None:
        return f"• {title}{suffix}"
    kind, begin, finish = parsed
    prefix = ""
    day = begin if kind == "allday" else begin.date()
    if with_date_prefix and day != today:
        prefix = f"{_WEEKDAYS[day.weekday()]} {day.day} {_MONTHS[day.month - 1]} · "
    if kind == "allday":
        return f"• {prefix}All day  {title}{suffix}"
    if finish is not None:
        return f"• {prefix}{begin:%H:%M}–{finish:%H:%M}  {title}{suffix}"
    return f"• {prefix}{begin:%H:%M}  {title}{suffix}"


def _calendar_ids(svc):
    """The calendars to read: every calendar on the user's list.

    Returns (ids, primary_id, complete). ``complete`` is False when
    the calendar list itself couldn't be fetched — the caller then
    reads only the primary calendar and the result says so, instead
    of silently presenting one calendar as the whole truth."""
    try:
        def fetch_page(token):
            kwargs = {}
            if token:
                kwargs["pageToken"] = token
            result = svc.calendarList().list(**kwargs).execute()
            return result.get("items") or [], result.get("nextPageToken")

        # GAPS-7: the calendar list itself is paged through the
        # shared engine — a long calendar list used to stop at the
        # API's first page with no way to continue.
        walked = paginate(fetch_page, page_size=DEFAULT_PAGE_SIZE)
        items = walked["items"]
        ids = [item.get("id") for item in items if item.get("id")]
        if ids:
            primary = next(
                (item.get("id") for item in items if item.get("primary")),
                ids[0],
            )
            return ids, primary, walked["complete"]
    except Exception:  # noqa: BLE001 — fall back to primary, disclosed
        pass
    return ["primary"], "primary", False


def _event_sort_key(event: dict):
    """Start-of-event ordering across calendars: timed events by
    their start, all-day events at their local midnight, anything
    unparseable last."""
    parsed = _event_start(event)
    if parsed is None:
        return (1, datetime.datetime.max.replace(tzinfo=_local_tz()))
    kind, begin, _finish = parsed
    if kind == "allday":
        return (0, datetime.datetime(
            begin.year, begin.month, begin.day, tzinfo=_local_tz()))
    return (0, begin)


def _fetch_events_paged(svc, start, end, max_per_calendar=None,
                        updated_min=None):
    """Events from every readable calendar in the window, merged in
    start order, through the shared sync engine (GAPS-7): each
    calendar is paged (pageToken until exhausted or
    max_per_calendar) with transient retries. Returns (items,
    report) — report: complete (False when any calendar was skipped
    or any page walk stopped early), scope_note (True when the
    calendar list itself or a secondary calendar failed — the
    caller's "only your primary calendar" trailer), partial (the
    engine's honest fragment when a page walk stopped early).

    A failure on the primary calendar with nothing fetched raises
    (the callers phrase it as a read failure, as before); a failure
    on a secondary calendar skips that calendar and marks the
    result incomplete, so one broken shared calendar can't sink the
    whole answer — and the answer discloses the gap. A page walk
    that fails after some pages keeps the pages it fetched and
    reports partial, instead of losing them to the raise."""
    ids, primary_id, list_complete = _calendar_ids(svc)
    items: list[dict] = []
    complete = list_complete
    scope_note = not list_complete
    partial = ""
    for cid in ids:
        page_size = DEFAULT_PAGE_SIZE
        if max_per_calendar:
            page_size = max(1, min(int(max_per_calendar),
                                   DEFAULT_PAGE_SIZE))

        def fetch_page(token, cid=cid):
            kwargs = dict(
                calendarId=cid,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=page_size,
            )
            if updated_min:
                kwargs["updatedMin"] = updated_min
            if token:
                kwargs["pageToken"] = token
            result = svc.events().list(**kwargs).execute()
            return (result.get("items") or [],
                    result.get("nextPageToken"))

        walked = paginate(fetch_page, page_size=page_size,
                          limit=max_per_calendar)
        if not walked["complete"]:
            complete = False
            if (walked["stopped"] == "error" and not walked["items"]
                    and cid == primary_id):
                raise RuntimeError(
                    walked["error"] or "calendar read failed")
            if walked["stopped"] == "error" and cid != primary_id:
                scope_note = True
            if not partial:
                partial = describe_partial(walked)
        items.extend(walked["items"])
    items.sort(key=_event_sort_key)
    return items, {"complete": complete, "scope_note": scope_note,
                   "partial": partial}


def _fetch_events(svc, start, end, max_per_calendar=None,
                  updated_min=None):
    """The old 2-tuple contract (items, complete) — kept for the
    callers outside this module (skills_system6_diag,
    skills_productivity3_focus_day). The callers in this module use
    _fetch_events_paged for the accurate partial trailer."""
    items, report = _fetch_events_paged(
        svc, start, end, max_per_calendar=max_per_calendar,
        updated_min=updated_min)
    return items, report["complete"]


_SCOPE_NOTE = (
    "Only your primary calendar could be read — events on your "
    "other calendars aren't shown."
)


def _calendar_trailer(report: dict) -> str:
    """The honest trailer for a calendar read: the scope note when
    only part of the calendars could be read, plus the engine's
    partial fragment when a page walk stopped early (GAPS-7).
    Either cause alone used to collapse into the scope note; a
    stopped page walk is a different gap and says so."""
    trailer = ""
    if report.get("scope_note"):
        trailer += f"\n({_SCOPE_NOTE})"
    if report.get("partial"):
        trailer += (f"\n(Couldn't load all events"
                    f"{report['partial']}.)")
    return trailer


def todays_schedule_data() -> dict:
    """Today's schedule as structured data — for the deterministic
    morning-briefing tool (round-3 fix Q6). Returns
    {"ok": True, "events": [...], "report": {...}} or
    {"ok": False, "error": "..."}; never raises. ``events`` are the
    raw Google event dicts for today, in start order; ``report`` is
    the _fetch_events_paged report (complete / scope_note / partial).
    The string form above stays the speakable path for
    get_todays_schedule; this accessor is the same fetch, structured,
    so the briefing tool can tell a genuinely empty day from a failed
    read instead of parsing a sentence."""
    svc, failure = _service_or_failure("calendar", "your calendar")
    if svc is None:
        return {"ok": False, "error": failure}
    now = datetime.datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(days=1)
    try:
        items, report = _fetch_events_paged(svc, start, end)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Couldn't read your calendar just now ({_short(e)})."}
    return {"ok": True, "events": items, "report": report}


def todays_schedule() -> str:
    """Everything on the user's calendars today, in start order."""
    svc, failure = _service_or_failure("calendar", "your calendar")
    if svc is None:
        return failure
    now = datetime.datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(days=1)
    try:
        items, report = _fetch_events_paged(svc, start, end)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read your calendar just now ({_short(e)})."
    if not items:
        return "Nothing on your calendar today." + (
            f" ({_SCOPE_NOTE})" if report["scope_note"] else "")
    today = now.date()
    # Event titles/locations are external content (anyone can send
    # the user an invite) — untrusted envelope (v5.3); VYUK's own
    # scope-note trailer stays outside it.
    body = injection.wrap_untrusted(
        "calendar", "\n".join(_format_event(ev, today, False) for ev in items))
    return body + _calendar_trailer(report)


def upcoming_events(days: int = 2, limit: int = 10) -> str:
    """What's coming up over the next ``days`` days (now → +days)."""
    svc, failure = _service_or_failure("calendar", "your calendar")
    if svc is None:
        return failure
    try:
        days = max(1, min(int(days), 14))
    except Exception:  # noqa: BLE001
        days = 2
    try:
        limit = max(1, min(int(limit), MAX_LIST_ITEMS))
    except Exception:  # noqa: BLE001
        limit = 10
    now = datetime.datetime.now().astimezone()
    end = now + datetime.timedelta(days=days)
    try:
        items, report = _fetch_events_paged(svc, now, end,
                                            max_per_calendar=limit)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read your calendar just now ({_short(e)})."
    items = items[:limit]
    if not items:
        return (
            f"Nothing on your calendar in the next {days} "
            f"day{'s' if days != 1 else ''}."
            + (f" ({_SCOPE_NOTE})" if report["scope_note"] else "")
        )
    today = now.date()
    body = injection.wrap_untrusted(
        "calendar", "\n".join(_format_event(ev, today, True) for ev in items))
    return body + _calendar_trailer(report)


def next_events(within_hours: int = 24, limit: int = 20) -> list[dict]:
    """Upcoming events as structured data — NOT a tool.

    Powers the window's calendar heads-ups (v2.5): the GUI polls this
    and speaks up shortly before something starts. Returns a list of
    {"id", "title", "start", "location", "all_day"} dicts, first
    start first, over now → now + ``within_hours``. ``start`` is
    always a timezone-aware datetime in local time (local midnight
    for all-day events). Unlike the speakable formatters above, this
    never returns a string and never raises — not connected, missing
    libraries, or any Google-side failure all come back as an empty
    list, which callers read as "nothing to say".
    """
    svc = _calendar_service_or_none()
    if svc is None:
        return []
    try:
        within_hours = max(1, min(int(within_hours), 24 * 7))
    except Exception:  # noqa: BLE001
        within_hours = 24
    try:
        limit = max(1, min(int(limit), 50))
    except Exception:  # noqa: BLE001
        limit = 20
    now = datetime.datetime.now().astimezone()
    end = now + datetime.timedelta(hours=within_hours)
    try:
        # GAPS-7: paged through the shared engine like every other
        # calendar read — a long upcoming list used to stop at the
        # first page. A failed walk still degrades to silence (the
        # heads-ups contract), with whatever pages did land.
        page_size = max(1, min(int(limit), DEFAULT_PAGE_SIZE))

        def fetch_page(token):
            kwargs = dict(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=end.isoformat(),
                maxResults=page_size,
                singleEvents=True,
                orderBy="startTime",
            )
            if token:
                kwargs["pageToken"] = token
            result = svc.events().list(**kwargs).execute()
            return (result.get("items") or [],
                    result.get("nextPageToken"))

        walked = paginate(fetch_page, page_size=page_size, limit=limit)
        raw_items = walked["items"]
    except Exception:  # noqa: BLE001 — heads-ups degrade to silence
        return []
    events: list[dict] = []
    for event in raw_items:
        parsed = _event_start(event)
        if parsed is None:
            continue
        kind, begin, _finish = parsed
        if kind == "allday":
            start = datetime.datetime(
                begin.year, begin.month, begin.day, tzinfo=_local_tz()
            )
            all_day = True
        else:
            start = begin
            all_day = False
        events.append(
            {
                "id": str(event.get("id") or ""),
                "title": (event.get("summary") or "(untitled event)").strip(),
                "start": start,
                "location": (event.get("location") or "").strip() or None,
                "all_day": all_day,
            }
        )
    events.sort(key=lambda ev: ev["start"])
    return events[:limit]


def create_event(title: str = "", start: str = "", end: str = "",
                 description: str = "", location: str = "",
                 calendar_id: str = "primary") -> str:
    """Creates a calendar event (v5.2 — one of the two write tools).

    Gated twice, by design: the executor's human confirm gate
    (core/funnel/tools_registry.py) must approve the call, and the stored sign-in
    must carry the calendar.events scope — a read-only sign-in gets
    the scope sentence, never a false "not connected". Times are
    ISO strings ("2026-10-23T17:15:00"); a timezone offset in the
    string is honoured, a naive time is read as local."""
    title = (title or "").strip()
    if not title:
        return "The event needs a title — what should I call it?"
    begin = _parse_event_dt(start or "")
    finish = _parse_event_dt(end or "")
    if begin is None or finish is None:
        return (
            "I couldn't understand those times — start and end need "
            "to be ISO times, like 2026-10-23T17:15:00."
        )
    if finish <= begin:
        return (
            "That event would end before it starts — check the "
            "start and end times."
        )
    svc, failure = _write_service_or_failure(
        "calendar", "your calendar", CALENDAR_EVENTS_SCOPE,
        "adding events")
    if svc is None:
        # F-F501: no event was created — the service could not be
        # built (not connected, sign-in expired, libraries
        # missing, or the write scope absent) — so the verdict is
        # failed, not a plain string the receipts would read
        # "succeeded". The failure sentence itself names the cause.
        from . import tool_outcome as _outcome  # R1b: seam wiring
        return _outcome.failed(failure, "calendar service unavailable")
    event = {
        "summary": title,
        "start": {"dateTime": begin.isoformat()},
        "end": {"dateTime": finish.isoformat()},
    }
    if description:
        event["description"] = description
    if location:
        event["location"] = location
    try:
        created = svc.events().insert(
            calendarId=calendar_id or "primary", body=event).execute()
    except Exception as e:  # noqa: BLE001
        if _is_scope_error(e):
            return _scope_missing_message("adding events")
        return f"Couldn't create that event just now ({_short(e)})."
    eid = (created or {}).get("id") or ""
    today = datetime.datetime.now().astimezone().date()
    line = _format_event(event, today, True)
    if line.startswith("• "):
        line = line[2:]
    result = f"Event created — {line}"
    if eid:
        result += f" (event id {eid})"
    return result + "."
