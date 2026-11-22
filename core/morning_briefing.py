"""The deterministic morning briefing (round-3 fix Q6, section-B B10).

The failure: the model was asked for a morning briefing and said
"your schedule is entirely clear" from `whats_pending` (reminders
only), never calling `get_todays_schedule` — in runs 2–3 of the
section-B day flows, despite an explicit instruction to call it.
There was no dedicated briefing tool; the model had to choose the
calendar tool and didn't reliably. Worse, `get_todays_schedule`
isn't resident in the desktop profile (the default Live profile),
so the model can only reach it through `use_toolbox`, which it does
not route a by-name demand through (field-check fix #10, V2 — the
`get_current_time_date` precedent).

The fix, per the parent ruling, is a DETERMINISTIC tool, not a
prompt change: `get_morning_briefing` reads the calendar itself
(the structured fetch, not a sentence to parse) and renders the
briefing from what the fetch actually returned:

- events with times, in start order (the existing `_format_event`
  lines), plus the free blocks between consecutive timed events
  and any overlaps named honestly;
- "No events scheduled today." ONLY when the fetch succeeded and
  returned zero events — never as a fallback;
- a fetch failure surfaces as the failure sentence (not connected /
  couldn't read), never as an empty day;
- nothing is invented: every line in the briefing comes from a
  fetched event or from the clock.

The render is a pure function of (now, events, report) so tests can
drive it with fabricated events; the handler is the thin seam that
fetches and renders. The tool is a read (no confirm gate).
"""

import datetime

from .google_services_calendar import (
    _calendar_trailer,
    _event_start,
    _format_event,
    _WEEKDAYS,
    _MONTHS,
    todays_schedule_data,
)


def _timed_span(event):
    """(start, end, title) for a timed event, else None."""
    parsed = _event_start(event)
    if parsed is None or parsed[0] != "timed":
        return None
    _kind, begin, finish = parsed
    title = (event.get("summary") or "(untitled event)").strip()
    return (begin, finish, title)


def render_morning_briefing(now, events, report, name=None,
                            money_lines=None):
    """The briefing text, deterministically, from the fetched day.

    ``now`` is a timezone-aware local datetime (the call time);
    ``events`` are the raw Google event dicts for today, in start
    order; ``report`` is the fetch report (scope_note / partial).
    Pure — no fetch, no clock read, so tests drive it directly.
    ``name`` (round-3 wizard expansion) is the declared user name
    from the first-run wizard: the header greets them by it when
    set, and reads the plain form when it is not. ``money_lines``
    (Stage 0 features batch 3, Group F) is the standing money
    alerts, as lines — the section appears only when there are
    lines, so a quiet morning shows no money section at all."""
    today = now.date()
    if now.hour < 12:
        greeting = "Good morning"
    elif now.hour < 18:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"
    if isinstance(name, str) and name.strip():
        greeting = f"{greeting}, {name.strip()}"
    header = (
        f"{greeting} — {_WEEKDAYS[now.weekday()]} {now.day} "
        f"{_MONTHS[now.month - 1]} {now.year}, {now:%H:%M}."
    )
    money_block = ""
    if money_lines:
        money_block = "\nMoney alerts:\n" + "\n".join(
            f"• {line}" for line in money_lines)
    if not events:
        return (header + "\nNo events scheduled today."
                + money_block + _calendar_trailer(report))
    lines = [header]
    lines.extend(_format_event(ev, today, False) for ev in events)
    # Free blocks + overlaps between consecutive TIMED events, in
    # start order. All-day events are listed, not timed, so they
    # take no part in the gap math. An open-ended event (no end)
    # ends the chain: nothing after it can be placed against it.
    timed = [span for span in (_timed_span(ev) for ev in events)
             if span is not None]
    for (prev_begin, prev_end, prev_title), (next_begin, _next_end,
                                             next_title) in zip(
                                                 timed, timed[1:]):
        if prev_end is None:
            break
        if next_begin > prev_end:
            lines.append(
                f"Free {prev_end:%H:%M}–{next_begin:%H:%M}.")
        elif next_begin < prev_end:
            lines.append(
                f"Overlaps: '{next_title}' ({next_begin:%H:%M}) "
                f"overlaps '{prev_title}' "
                f"({prev_begin:%H:%M}–{prev_end:%H:%M}).")
    return "\n".join(lines) + money_block + _calendar_trailer(report)


def get_morning_briefing() -> str:
    """The tool handler: fetch today, render the briefing.

    Never raises and never claims an empty day on a failed fetch:
    a failed fetch returns the failure sentence verbatim (not
    connected / couldn't read), so the model reports the failure
    instead of improvising "your schedule is clear".
    """
    data = todays_schedule_data()
    if not data["ok"]:
        return data["error"]
    now = datetime.datetime.now().astimezone()
    try:
        from .config import config as _config

        name = str(getattr(_config, "user_name", "") or "")
    except Exception:  # noqa: BLE001 — no name reads as unnamed
        name = ""
    # Stage 0 features batch 3 (Group F): the standing money
    # alerts ride the briefing — computed from last-known prices,
    # no fetch here, and a money-read failure carries one honest
    # line instead of failing the whole briefing.
    try:
        from .money_alerts import money_alert_lines_for_briefing

        money_lines = money_alert_lines_for_briefing(now)
    except Exception:  # noqa: BLE001 — the briefing must not fail on money
        money_lines = ["Money alerts couldn't be read just now."]
    return render_morning_briefing(now, data["events"], data["report"],
                                   name=name or None,
                                   money_lines=money_lines)
