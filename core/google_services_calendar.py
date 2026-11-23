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


def events_in_range(start_date, end_date, limit: int = 200) -> list[dict]:
    """Events in a date window, as structured data — NOT a tool.

    Powers the Calendar view (Stage 0 features batch 1): the month
    grid needs a whole month, and ``next_events`` caps its window
    at 7 days. Same dict shape as ``next_events`` ({"id", "title",
    "start", "location", "all_day"}), first start first, over
    ``start_date`` 00:00 → ``end_date`` 23:59:59 local time. Same
    never-raises contract: not connected, missing libraries, or any
    Google-side failure all come back as an empty list.
    """
    svc = _calendar_service_or_none()
    if svc is None:
        return []
    try:
        limit = max(1, min(int(limit), 500))
    except Exception:  # noqa: BLE001
        limit = 200
    try:
        start_dt = datetime.datetime(
            start_date.year, start_date.month, start_date.day,
            tzinfo=_local_tz())
        end_dt = datetime.datetime(
            end_date.year, end_date.month, end_date.day,
            23, 59, 59, tzinfo=_local_tz())
    except Exception:  # noqa: BLE001
        return []
    if end_dt < start_dt:
        return []
    try:
        items, _complete = _fetch_events(svc, start_dt, end_dt)
    except Exception:  # noqa: BLE001 — a failed walk degrades to silence
        return []
    events: list[dict] = []
    for event in items or []:
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


def _is_self_declined(event: dict) -> bool:
    """True when the user declined this event: an attendee entry
    marked self whose responseStatus is "declined". Events the
    user created carry no self attendee (or an accepted one), so
    they never read as declined here."""
    try:
        for attendee in event.get("attendees") or []:
            if not isinstance(attendee, dict):
                continue
            if attendee.get("self") and str(
                    attendee.get("responseStatus") or "").strip(
                    ).lower() == "declined":
                return True
    except Exception:  # noqa: BLE001 — a malformed attendee list
        return False  # never reads as declined
    return False


def todays_timed_events() -> list[dict]:
    """Today's TIMED events as structured data — NOT a tool.

    Powers calendar-aware quiet (Stage 0 features batch 6,
    item 14): the quiet gate needs start AND end to know when
    a meeting is in progress, which ``next_events`` /
    ``events_in_range`` do not carry (their dicts have start
    only). Same never-raises contract: not connected, missing
    libraries, or any Google-side failure all come back as an
    empty list, which the gate reads as "no calendar".

    The shape is the ``next_events`` dict PLUS "end": {"id",
    "title", "start", "end", "location", "all_day": False}.
    ``start`` / ``end`` are timezone-aware datetimes in local
    time. Dropped, by the rulings: all-day events (an all-day
    "holiday" must never silence the whole day), declined
    events (the user's own "no"), cancelled events, and timed
    events with no parseable end (in-progress cannot be
    established without one). Window is today 00:00 → tomorrow
    00:00 local; the API's overlap semantics also return an
    event that started yesterday and runs past midnight.
    """
    svc = _calendar_service_or_none()
    if svc is None:
        return []
    try:
        now = datetime.datetime.now().astimezone()
        start_dt = now.replace(hour=0, minute=0, second=0,
                               microsecond=0)
        end_dt = start_dt + datetime.timedelta(days=1)
        items, _complete = _fetch_events(svc, start_dt, end_dt)
    except Exception:  # noqa: BLE001 — a failed read degrades
        return []  # to silence (no calendar), never a raise
    events: list[dict] = []
    for event in items or []:
        if not isinstance(event, dict):
            continue
        if str(event.get("status") or "").strip().lower() \
                == "cancelled":
            continue
        if _is_self_declined(event):
            continue
        parsed = _event_start(event)
        if parsed is None:
            continue
        kind, begin, finish = parsed
        if kind != "timed" or finish is None:
            continue
        events.append(
            {
                "id": str(event.get("id") or ""),
                "title": (event.get("summary")
                          or "(untitled event)").strip(),
                "start": begin,
                "end": finish,
                "location": (event.get("location") or "").strip()
                or None,
                "all_day": False,
            }
        )
    events.sort(key=lambda ev: ev["start"])
    return events


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


def _day_bounds(day: datetime.date):
    """(start, end) datetimes for one local day, 00:00 → 23:59:59."""
    tz = _local_tz()
    start = datetime.datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + datetime.timedelta(days=1) - datetime.timedelta(
        seconds=1)
    return start, end


def _parse_day(value: str):
    """An ISO date (\"2026-11-23\") → date, else None. A bare
    datetime is read as its local date."""
    try:
        return datetime.date.fromisoformat((value or "").strip()[:10])
    except Exception:  # noqa: BLE001
        return None


def find_free_slots(date: str = "", duration_minutes: int = 60,
                    start_hour: int = 8, end_hour: int = 20) -> str:
    """Free blocks on one day that fit a meeting of
    ``duration_minutes`` minutes, between ``start_hour`` and
    ``end_hour`` local time. A READ — the free/busy answer itself
    is the owner's schedule, so the funnel's owner-read gate
    (L-24) refuses it in an unproven live-origin session; the
    function itself never writes. A failed or partial calendar
    walk is reported as a failure, never as a free day — the
    morning-briefing precedent (a failed fetch is never an empty
    claim). All-day events block the whole day; a timed event
    with no end is read as one hour.
    """
    day = _parse_day(date) if date else datetime.datetime.now(
        ).astimezone().date()
    if day is None:
        return ("I couldn't understand that date — give it as "
                "2026-11-23.")
    try:
        duration = int(duration_minutes)
    except Exception:  # noqa: BLE001
        duration = 60
    duration = max(5, min(duration, 480))
    try:
        start_h = int(start_hour)
        end_h = int(end_hour)
    except Exception:  # noqa: BLE001
        start_h, end_h = 8, 20
    start_h = max(0, min(start_h, 23))
    end_h = max(1, min(end_h, 24))
    if end_h <= start_h:
        return "The search window ends before it starts — check the hours."
    svc, failure = _service_or_failure("calendar", "your calendar")
    if svc is None:
        return failure
    day_start, day_end = _day_bounds(day)
    try:
        items, complete = _fetch_events(svc, day_start, day_end)
    except Exception as e:  # noqa: BLE001
        return f"Couldn't read your calendar just now ({_short(e)})."
    window_start = day_start.replace(hour=start_h, minute=0)
    window_end = day_start.replace(hour=end_h, minute=0) \
        if end_h < 24 else day_start + datetime.timedelta(days=1)
    busy: list[tuple[datetime.datetime, datetime.datetime]] = []
    for event in items or []:
        parsed = _event_start(event)
        if parsed is None:
            continue
        kind, begin, finish = parsed
        if kind == "allday":
            return (f"No free time on {day.isoformat()} — "
                    f"\"{event.get('summary') or '(untitled event)'}\" "
                    "is an all-day event.")
        if finish is None:
            finish = begin + datetime.timedelta(hours=1)
        start = max(begin, window_start)
        end = min(finish, window_end)
        if end > start:
            busy.append((start, end))
    busy.sort()
    merged: list[list[datetime.datetime]] = []
    for start, end in busy:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    free: list[tuple[datetime.datetime, datetime.datetime]] = []
    cursor = window_start
    for start, end in merged:
        if start - cursor >= datetime.timedelta(minutes=duration):
            free.append((cursor, start))
        cursor = max(cursor, end)
    if window_end - cursor >= datetime.timedelta(minutes=duration):
        free.append((cursor, window_end))

    def _hhmm(when):
        return f"{when:%H:%M}"

    label = day.isoformat()
    if not free:
        line = (f"No {duration}-minute free block on {label} between "
                f"{start_h:02d}:00 and {end_h:02d}:00 — the day is full.")
    else:
        slots = ", ".join(
            f"{_hhmm(s)}–{_hhmm(e)}" for s, e in free)
        line = (f"Free on {label} for {duration} minutes: {slots}.")
    if not complete:
        line += (" (Some of your calendars couldn't be read, so "
                 "this may miss events on them.)")
    return line


def _resolve_event(svc, event: str, date: str, calendar_id: str):
    """Raw Google events matching a title fragment on one day — or,
    with no date, today through the next 7 days. Returns
    (matches, failure): matches is the list (possibly empty),
    failure a sentence when the walk itself failed."""
    fragment = (event or "").strip().lower()
    if date:
        day = _parse_day(date)
        if day is None:
            return [], "I couldn't understand that date — give it as 2026-11-23."
        start, end = _day_bounds(day)
    else:
        today = datetime.datetime.now().astimezone().date()
        start, _ = _day_bounds(today)
        _, end = _day_bounds(today + datetime.timedelta(days=7))
    try:
        items, _complete = _fetch_events(svc, start, end)
    except Exception as e:  # noqa: BLE001
        return [], f"Couldn't read your calendar just now ({_short(e)})."
    matches = [ev for ev in items or []
               if fragment in str(ev.get("summary") or "").lower()]
    return matches, ""


def _describe_raw_event(event: dict) -> str:
    """\"Dentist, 23 Nov 15:00–16:00\" for a raw Google event."""
    title = (event.get("summary") or "(untitled event)").strip()
    parsed = _event_start(event)
    if parsed is None:
        return title
    kind, begin, finish = parsed
    if kind == "allday":
        return f"{title}, {begin.isoformat()} (all day)"
    if finish is None:
        return f"{title}, {begin:%d %b %H:%M}"
    return f"{title}, {begin:%d %b %H:%M}–{finish:%H:%M}"


def _get_event(svc, calendar_id: str, event_id: str):
    """One event by id, or None when Google says it is gone
    (404) or the fetch fails. The 404 is the proof a delete
    took — it is a return value here, never a raise."""
    try:
        return svc.events().get(
            calendarId=calendar_id or "primary",
            eventId=event_id).execute()
    except Exception as e:  # noqa: BLE001
        status = getattr(getattr(e, "resp", None), "status", None)
        if status == 404 or "404" in str(e):
            return None
        return {"__fetch_error__": _short(e)}


def cancel_event(event: str = "", date: str = "", event_id: str = "",
                 calendar_id: str = "primary") -> str:
    """Cancels one calendar event, by id or by title (+ date).
    Confirm-gated at the funnel (like create_calendar_event) and
    never in a plan's never-ask set. Every failure is an
    _outcome.failed — a plain string would be read as success by
    the receipts while nothing was cancelled (F-F501). The delete
    is verified by re-read: the event must be gone (a GET that
    404s) before the success line names it (A5-sysreadback)."""
    from . import tool_outcome as _outcome  # R1b: seam wiring
    if not (event or "").strip() and not (event_id or "").strip():
        return _outcome.failed(
            "Which event should I cancel? Give me its title "
            "(and the day, if it could be more than one).",
            "no event named")
    svc, failure = _write_service_or_failure(
        "calendar", "your calendar", CALENDAR_EVENTS_SCOPE,
        "cancelling events")
    if svc is None:
        return _outcome.failed(failure, "calendar service unavailable")
    cid = calendar_id or "primary"
    if (event_id or "").strip():
        found = _get_event(svc, cid, event_id.strip())
        if found is None:
            return _outcome.failed(
                "That event is already gone — nothing to cancel.",
                "event not found")
        if isinstance(found, dict) and found.get("__fetch_error__"):
            return _outcome.failed(
                f"Couldn't check that event just now "
                f"({found['__fetch_error__']}).",
                "event fetch failed")
        target, title = found, _describe_raw_event(found)
    else:
        matches, walk_failure = _resolve_event(svc, event, date, cid)
        if walk_failure:
            return _outcome.failed(walk_failure, "calendar read failed")
        if not matches:
            when = f" on {date}" if date else ""
            return _outcome.failed(
                f"I couldn't find \"{event}\"{when} on your calendar.",
                "event not found")
        if len(matches) > 1:
            lines = "\n".join(
                f"• {_describe_raw_event(ev)}" for ev in matches[:5])
            return _outcome.failed(
                f"That matches {len(matches)} events — which one?\n{lines}",
                "ambiguous event")
        target = matches[0]
        title = _describe_raw_event(target)
    eid = str(target.get("id") or "")
    if not eid:
        return _outcome.failed(
            "That event has no id I can cancel by — try it from "
            "the calendar itself.",
            "event has no id")
    try:
        svc.events().delete(calendarId=cid, eventId=eid).execute()
    except Exception as e:  # noqa: BLE001
        if _is_scope_error(e):
            return _outcome.failed(
                _scope_missing_message("cancelling events"),
                "scope missing")
        return _outcome.failed(
            f"Couldn't cancel that event just now ({_short(e)}).",
            "delete failed")
    gone = _get_event(svc, cid, eid)
    if gone is not None:
        if isinstance(gone, dict) and gone.get("__fetch_error__"):
            return _outcome.failed(
                f"The cancel went through but I couldn't verify it "
                f"({gone['__fetch_error__']}) — check the calendar.",
                "cancel unverified")
        return _outcome.failed(
            "The cancel didn't take — the event is still there.",
            "cancel unverified")
    return f"Cancelled — {title}."


def reschedule_event(event: str = "", date: str = "", event_id: str = "",
                     new_start: str = "", new_end: str = "",
                     calendar_id: str = "primary") -> str:
    """Moves one calendar event to a new time, by id or by title
    (+ date). With no new_end the whole event shifts by the same
    amount as new_start. Confirm-gated at the funnel (like
    create_calendar_event) and never in a plan's never-ask set.
    Every failure is an _outcome.failed (F-F501). The move is
    verified by re-read: a GET after the patch must show the new
    times before the success line names them (A5-sysreadback).
    An all-day event moves by date only — a timed move for one
    is refused honestly."""
    from . import tool_outcome as _outcome  # R1b: seam wiring
    if not (event or "").strip() and not (event_id or "").strip():
        return _outcome.failed(
            "Which event should I move? Give me its title "
            "(and the day, if it could be more than one).",
            "no event named")
    begin = _parse_event_dt(new_start or "")
    if begin is None:
        return _outcome.failed(
            "I couldn't understand the new start — give it as an "
            "ISO time, like 2026-11-23T17:00:00.",
            "new start unparseable")
    finish = _parse_event_dt(new_end or "") if new_end else None
    if new_end and finish is None:
        return _outcome.failed(
            "I couldn't understand the new end — give it as an "
            "ISO time, like 2026-11-23T18:00:00.",
            "new end unparseable")
    svc, failure = _write_service_or_failure(
        "calendar", "your calendar", CALENDAR_EVENTS_SCOPE,
        "moving events")
    if svc is None:
        return _outcome.failed(failure, "calendar service unavailable")
    cid = calendar_id or "primary"
    if (event_id or "").strip():
        found = _get_event(svc, cid, event_id.strip())
        if found is None:
            return _outcome.failed(
                "That event is already gone — nothing to move.",
                "event not found")
        if isinstance(found, dict) and found.get("__fetch_error__"):
            return _outcome.failed(
                f"Couldn't check that event just now "
                f"({found['__fetch_error__']}).",
                "event fetch failed")
        target = found
    else:
        matches, walk_failure = _resolve_event(svc, event, date, cid)
        if walk_failure:
            return _outcome.failed(walk_failure, "calendar read failed")
        if not matches:
            when = f" on {date}" if date else ""
            return _outcome.failed(
                f"I couldn't find \"{event}\"{when} on your calendar.",
                "event not found")
        if len(matches) > 1:
            lines = "\n".join(
                f"• {_describe_raw_event(ev)}" for ev in matches[:5])
            return _outcome.failed(
                f"That matches {len(matches)} events — which one?\n{lines}",
                "ambiguous event")
        target = matches[0]
    eid = str(target.get("id") or "")
    if not eid:
        return _outcome.failed(
            "That event has no id I can move by — try it from "
            "the calendar itself.",
            "event has no id")
    old = _describe_raw_event(target)
    parsed = _event_start(target)
    if parsed is not None and parsed[0] == "allday":
        if new_end or ":" in (new_start or "")[10:]:
            return _outcome.failed(
                "That's an all-day event — move it by date "
                f"({parsed[1].isoformat()}), not by time.",
                "all-day event needs a date")
        new_day = _parse_day(new_start)
        if new_day is None:
            return _outcome.failed(
                "I couldn't understand the new date — give it as "
                "2026-11-24.",
                "new date unparseable")
        body = {"start": {"date": new_day.isoformat()},
                "end": {"date": (new_day
                                 + datetime.timedelta(days=1)).isoformat()}}
        # want[0] is a date here, matching what _event_start
        # returns for an all-day event — an isoformat string
        # would never equal it and every all-day move would
        # fail verification after landing.
        want = (new_day, None)
    else:
        if finish is None:
            if parsed is not None:
                _kind, old_begin, old_finish = parsed
                if old_begin is not None and old_finish is not None:
                    finish = begin + (old_finish - old_begin)
                else:
                    finish = begin + datetime.timedelta(hours=1)
            else:
                # The old event carries nothing parseable — move
                # it as a one-hour block at the new start.
                finish = begin + datetime.timedelta(hours=1)
        if finish is not None and finish <= begin:
            return _outcome.failed(
                "That move would end before it starts — check the "
                "new times.",
                "new end before new start")
        body = {"start": {"dateTime": begin.isoformat()},
                "end": {"dateTime": finish.isoformat()}}
        want = (begin, finish)
    try:
        svc.events().patch(
            calendarId=cid, eventId=eid, body=body).execute()
    except Exception as e:  # noqa: BLE001
        if _is_scope_error(e):
            return _outcome.failed(
                _scope_missing_message("moving events"),
                "scope missing")
        return _outcome.failed(
            f"Couldn't move that event just now ({_short(e)}).",
            "move failed")
    moved = _get_event(svc, cid, eid)
    if moved is None:
        return _outcome.failed(
            "The move went through but the event is gone now — "
            "check the calendar.",
            "move unverified")
    if isinstance(moved, dict) and moved.get("__fetch_error__"):
        return _outcome.failed(
            f"The move went through but I couldn't verify it "
            f"({moved['__fetch_error__']}) — check the calendar.",
            "move unverified")
    check = _event_start(moved)
    if check is None:
        return _outcome.failed(
            "The move went through but I couldn't read the new "
            "time back — check the calendar.",
            "move unverified")
    if check[0] == "allday":
        if check[1] != want[0]:
            return _outcome.failed(
                "The move didn't take — the event is still on the "
                "old day.",
                "move unverified")
        new_label = f"{check[1].isoformat()} (all day)"
    else:
        if check[1] != want[0] or check[2] != want[1]:
            return _outcome.failed(
                "The move didn't take — the event is still at the "
                "old time.",
                "move unverified")
        new_label = (f"{check[1]:%d %b %H:%M}–{check[2]:%H:%M}"
                     if check[2] is not None
                     else f"{check[1]:%d %b %H:%M}")
    return f"Moved — {old} to {new_label}."
