"""Round-3 fix Q6 (2026-11-19, parent ruling: a DETERMINISTIC
briefing tool, NOT a prompt change).

Section-B B10 runs 2–3: the model was asked for a morning briefing
— "First call your get_todays_schedule tool" — and said "your
schedule is entirely clear" from `whats_pending` (reminders only),
never calling the calendar tool. There was no dedicated briefing
tool, and `get_todays_schedule` isn't resident in the desktop
profile (the default Live profile), so the model could only reach
it through `use_toolbox`, which it does not route a by-name demand
through (field-check fix #10, V2 — the `get_current_time_date`
precedent).

This suite pins the fix, so it can never regress silently:

- `get_morning_briefing` exists, is a read (never confirm-gated),
  and is resident in BOTH profiles (assistant CORE_TOOL_PRIORITY
  and desktop DESKTOP_TOOL_PRIORITY), with the snapshot entry to
  match.
- The render is deterministic from the fetched day: seeded events
  list with times, the free block between them is named, an
  overlap is named, an all-day event takes no part in the gap
  math, and the greeting follows the call time.
- An empty day renders "No events scheduled today." ONLY when the
  fetch succeeded with zero events; a failed fetch renders the
  failure sentence and never claims the day is empty.
- The tool never invents an event: every event line comes from a
  fetched event.
- The lite router sends a morning-briefing ask to the briefing
  tool, while the bare schedule ask still goes to
  get_todays_schedule.
- The toolbox ranking surfaces the briefing tool first for
  "give me my morning briefing" and keeps get_todays_schedule
  first for "what's my schedule today".

Stdlib unittest only. The render tests drive the pure function
with fabricated events (no Google, no clock); the handler tests
use the same fake-service seam as repro_6.py (monkeypatched
`_calendar_service_or_none`). No data/ writes.
"""

import datetime
import unittest

from core.funnel.tool_classification import (  # noqa: E402
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
)
from core.funnel.toolbox_tokens import CORE_TOOL_PRIORITY  # noqa: E402
from core.funnel.tool_profiles import DESKTOP_TOOL_PRIORITY  # noqa: E402
from core.funnel.tools_registry import tools  # noqa: E402
from core.funnel import toolbox_tokens as _tokens  # noqa: E402
from core.morning_briefing import (  # noqa: E402
    get_morning_briefing,
    render_morning_briefing,
)
from core.voice.lite_client_helpers import _route_intent  # noqa: E402
import core.google_services_services as _svc_mod  # noqa: E402

_TZ = datetime.datetime.now().astimezone().tzinfo


def _now(hour, minute=0):
    return datetime.datetime(2026, 11, 19, hour, minute, tzinfo=_TZ)


def _event(title, sh, sm, eh, em, location=None):
    ev = {"summary": title,
          "start": {"dateTime": _now(sh, sm).isoformat()},
          "end": {"dateTime": _now(eh, em).isoformat()}}
    if location:
        ev["location"] = location
    return ev


def _allday(title):
    return {"summary": title, "start": {"date": "2026-11-19"},
            "end": {"date": "2026-11-20"}}


_EMPTY_REPORT = {"complete": True, "scope_note": False, "partial": ""}


class _FakeEvents:
    def __init__(self, items):
        self._items = items

    def list(self, **kwargs):
        return self

    def execute(self):
        return {"items": list(self._items)}


class _FakeCalendarList:
    def list(self, **kwargs):
        return self

    def execute(self):
        return {"items": [{"id": "primary", "primary": True}]}


class _FakeService:
    def __init__(self, items):
        self._items = items

    def calendarList(self):
        return _FakeCalendarList()

    def events(self):
        return _FakeEvents(self._items)


class TestBriefingToolWiring(unittest.TestCase):
    def test_tool_is_registered(self):
        names = {d["name"] for d in tools.get_declarations()}
        self.assertIn("get_morning_briefing", names)

    def test_tool_is_a_read_never_confirm_gated(self):
        self.assertIn("get_morning_briefing", _READ_ONLY_TOOLS)
        self.assertNotIn("get_morning_briefing", _CONFIRM_GATE_TOOLS)
        self.assertNotIn("get_morning_briefing", _FREE_BY_DESIGN)

    def test_tool_is_resident_in_both_profiles(self):
        self.assertIn("get_morning_briefing", CORE_TOOL_PRIORITY)
        self.assertIn("get_morning_briefing", DESKTOP_TOOL_PRIORITY)

    def test_snapshot_entry_is_read(self):
        import json
        import pathlib
        snap = json.loads(pathlib.Path(
            __file__).resolve().parent.joinpath(
                "_classification_snapshot.json").read_text())
        self.assertEqual(snap["get_morning_briefing"],
                         {"confirm": False, "free": None, "read": True})

    def test_tool_takes_no_arguments(self):
        decl = next(d for d in tools.get_declarations()
                    if d["name"] == "get_morning_briefing")
        self.assertEqual(decl.get("parameters", {}).get("properties", {}),
                         {})


class TestBriefingRender(unittest.TestCase):
    def test_seeded_events_list_with_times(self):
        out = render_morning_briefing(_now(7), [
            _event("Team standup", 9, 0, 10, 0, "Room 3"),
            _event("Dentist appointment", 14, 30, 15, 30),
        ], _EMPTY_REPORT)
        self.assertIn("Team standup", out)
        self.assertIn("09:00", out)
        self.assertIn("Room 3", out)
        self.assertIn("Dentist appointment", out)
        self.assertIn("14:30", out)
        self.assertIn("Good morning", out)

    def test_free_block_between_events_is_named(self):
        out = render_morning_briefing(_now(7), [
            _event("Team standup", 9, 0, 10, 0),
            _event("Dentist appointment", 14, 30, 15, 30),
        ], _EMPTY_REPORT)
        self.assertIn("Free 10:00–14:30.", out)

    def test_no_free_block_when_events_touch(self):
        out = render_morning_briefing(_now(7), [
            _event("First", 9, 0, 10, 0),
            _event("Second", 10, 0, 11, 0),
        ], _EMPTY_REPORT)
        self.assertNotIn("Free", out)

    def test_overlap_is_named_not_hidden(self):
        out = render_morning_briefing(_now(7), [
            _event("Team standup", 9, 0, 10, 0),
            _event("Urgent call", 9, 30, 10, 30),
        ], _EMPTY_REPORT)
        self.assertIn("Overlaps:", out)
        self.assertIn("Urgent call", out)
        self.assertIn("Team standup", out)

    def test_all_day_event_takes_no_part_in_gap_math(self):
        out = render_morning_briefing(_now(7), [
            _allday("Diwali"),
            _event("Team standup", 9, 0, 10, 0),
            _event("Dentist appointment", 14, 30, 15, 30),
        ], _EMPTY_REPORT)
        self.assertIn("All day", out)
        self.assertIn("Diwali", out)
        self.assertIn("Free 10:00–14:30.", out)

    def test_empty_day_says_no_events(self):
        out = render_morning_briefing(_now(7), [], _EMPTY_REPORT)
        self.assertIn("No events scheduled today.", out)

    def test_greeting_follows_the_call_time(self):
        self.assertIn("Good morning",
                      render_morning_briefing(_now(7), [], _EMPTY_REPORT))
        self.assertIn("Good afternoon",
                      render_morning_briefing(_now(13), [], _EMPTY_REPORT))
        self.assertIn("Good evening",
                      render_morning_briefing(_now(20), [], _EMPTY_REPORT))

    def test_scope_note_is_disclosed_not_silent(self):
        report = {"complete": False, "scope_note": True, "partial": ""}
        out = render_morning_briefing(_now(7), [], report)
        self.assertIn("Only your primary calendar", out)


class TestBriefingHandler(unittest.TestCase):
    def setUp(self):
        self._real = _svc_mod._calendar_service_or_none

    def tearDown(self):
        _svc_mod._calendar_service_or_none = self._real

    def test_handler_lists_seeded_events(self):
        _svc_mod._calendar_service_or_none = lambda: _FakeService([
            _event("Team standup", 9, 0, 10, 0, "Room 3"),
            _event("Dentist appointment", 14, 30, 15, 30),
        ])
        out = get_morning_briefing()
        self.assertIn("Team standup", out)
        self.assertIn("Dentist appointment", out)
        self.assertIn("Free 10:00–14:30.", out)

    def test_handler_empty_day_is_honest(self):
        _svc_mod._calendar_service_or_none = lambda: _FakeService([])
        out = get_morning_briefing()
        self.assertIn("No events scheduled today.", out)

    def test_handler_failure_never_claims_empty(self):
        _svc_mod._calendar_service_or_none = lambda: None
        out = get_morning_briefing()
        self.assertNotIn("No events scheduled", out)
        self.assertNotIn("schedule is clear", out.lower())
        self.assertNotIn("entirely clear", out.lower())
        self.assertTrue("isn't connected" in out.lower()
                        or "couldn't" in out.lower(), out)

    def test_handler_invents_nothing(self):
        _svc_mod._calendar_service_or_none = lambda: _FakeService([
            _event("Team standup", 9, 0, 10, 0),
        ])
        out = get_morning_briefing()
        self.assertNotIn("Lunch with Priya", out)
        self.assertNotIn("Dentist", out)


class TestBriefingRouting(unittest.TestCase):
    def test_lite_router_sends_briefing_ask_to_the_tool(self):
        self.assertEqual(
            _route_intent("Good morning, give me my morning briefing"),
            ("get_morning_briefing", {}))
        self.assertEqual(
            _route_intent("what's on today?"),
            ("get_morning_briefing", {}))

    def test_lite_router_keeps_bare_schedule_ask(self):
        self.assertEqual(
            _route_intent("what's my schedule today"),
            ("get_todays_schedule", {}))
        self.assertEqual(
            _route_intent("show me my calendar"),
            ("get_todays_schedule", {}))

    def test_toolbox_ranking_surfaces_briefing_first(self):
        ranked = _tokens.rank_tools_keyword(
            tools, "give me my morning briefing", limit=8)
        self.assertIn("get_morning_briefing", ranked[:3], ranked)

    def test_toolbox_ranking_keeps_schedule_first_for_schedule_ask(self):
        ranked = _tokens.rank_tools_keyword(
            tools, "what's my schedule today", limit=8)
        self.assertEqual(ranked[0], "get_todays_schedule", ranked)


if __name__ == "__main__":
    unittest.main()
