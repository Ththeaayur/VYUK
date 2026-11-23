"""Stage 0 features batch 2 (Group E, 2026-11-22): mail and calendar
actions — items 19 (free-slot finder), 20 (email snooze), 22
(cancel/delete calendar event), 23 (reschedule/move event). Item 21
(threaded reply) already exists and was not rebuilt.

This suite pins the batch, so it can never regress silently:

- All four tools are registered, classified (free-slot a read,
  snooze free app-state, cancel/move confirm-gated and never
  planned), labelled, grouped (Your world), synonymed, and in the
  evidence table + snapshot with the right class.
- The free-slot finder is owner-only by construction (L-24): it
  joins _OWNER_ONLY_READS, so a free/busy answer is refused in an
  unproven live-origin session (the funnel leg lives in the repro
  probe; this suite pins the gate set the funnel reads).
- The free-slot math is deterministic from seeded events: blocks
  between events, the duration filter, an all-day event blocking
  the whole day, a timed event with no end reading as one hour —
  and a failed fetch is a failure, never a free day.
- The snooze store round-trips on a temp path (save / load / list
  / unsnooze / expiry), the handler snoozes and brings back early
  through the fake Gmail service, and snoozed mail is hidden from
  get_unread_email and recent_messages while explicit search still
  finds it.
- Cancel and move resolve by id or by title + date (0 and >1
  matches both fail honestly), verify by re-read GET after the act
  (cancel needs the 404, the move needs the new times), patch
  start/end only, shift the whole event when no new end is given,
  and refuse a timed move of an all-day event. Every failure is an
  _outcome.failed, never a plain success string (F-F501).
- Census pins: 769 tools / 384 read / 385 effectful / 169
  confirm-gated / params 2121 / grouped 766 / closed 768 /
  already-verified 319 / §2c rows 385 / labels 694 /
  comms_calendar 32.

Stdlib unittest only. All Google calls go through fake services
(monkeypatched _service_or_failure); all snooze-store writes go
through a temp path (monkeypatched _snooze_path). No data/ writes,
no network.
"""

import datetime
import json
import pathlib
import re
import tempfile
import unittest

from core import tool_outcome as _outcome  # noqa: E402
from core.funnel import tools_gate as _gate  # noqa: E402
from core.funnel import toolbox_tokens as _tokens  # noqa: E402
from core import plan_approval as _pa  # noqa: E402
from core.funnel.tool_classification import (  # noqa: E402
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
)
from core.funnel.tool_profiles import DESKTOP_TOOL_PRIORITY  # noqa: E402
from core.funnel.tools_registry import tools  # noqa: E402
from core import tool_labels as _labels  # noqa: E402
from core.catalogue_groups_data import GROUP_MEMBERS  # noqa: E402
import core.google_services_calendar as _cal  # noqa: E402
import core.google_services_gmail as _gmail  # noqa: E402

_TZ = datetime.datetime.now().astimezone().tzinfo
_DAY = "2026-11-23"


def _dt(hour, minute=0):
    return datetime.datetime(2026, 11, 23, hour, minute, tzinfo=_TZ)


def _event(title, sh, sm, eh, em, event_id=None, desc=None):
    ev = {"id": event_id or title.lower().replace(" ", "-"),
          "summary": title,
          "start": {"dateTime": _dt(sh, sm).isoformat()},
          "end": {"dateTime": _dt(eh, em).isoformat()}}
    if desc:
        ev["description"] = desc
    return ev


def _allday(title, event_id=None):
    return {"id": event_id or title.lower().replace(" ", "-"),
            "summary": title,
            "start": {"date": _DAY},
            "end": {"date": "2026-11-24"}}


def _no_end(title, sh, sm):
    return {"id": title.lower().replace(" ", "-"),
            "summary": title,
            "start": {"dateTime": _dt(sh, sm).isoformat()},
            "end": {}}


class _FakeCalendarList:
    def list(self, **kwargs):
        return self

    def execute(self):
        return {"items": [{"id": "primary", "primary": True}]}


class _FakeCalEvents:
    """One resource serving list/get/delete/patch over a dict of
    events, keyed by id. get raises when the id is gone (the 404
    the cancel/move verification reads); delete removes it; patch
    replaces start/end only and returns the updated event."""

    def __init__(self, items):
        self._items = {e["id"]: dict(e) for e in items}
        self._op = None
        self._kwargs = {}

    def list(self, **kwargs):
        self._op, self._kwargs = "list", kwargs
        return self

    def get(self, **kwargs):
        self._op, self._kwargs = "get", kwargs
        return self

    def delete(self, **kwargs):
        self._op, self._kwargs = "delete", kwargs
        return self

    def patch(self, **kwargs):
        self._op, self._kwargs = "patch", kwargs
        return self

    def execute(self):
        if self._op == "list":
            return {"items": list(self._items.values())}
        if self._op == "get":
            ev = self._items.get(self._kwargs.get("eventId"))
            if ev is None:
                raise Exception("404: event not found")
            return dict(ev)
        if self._op == "delete":
            self._items.pop(self._kwargs.get("eventId"), None)
            return {}
        if self._op == "patch":
            ev = self._items.get(self._kwargs.get("eventId"))
            if ev is None:
                raise Exception("404: event not found")
            body = self._kwargs.get("body") or {}
            ev["start"] = body.get("start", ev["start"])
            ev["end"] = body.get("end", ev["end"])
            self._items[ev["id"]] = ev
            return dict(ev)
        raise AssertionError(f"unexpected op {self._op}")


class _FakeCalService:
    def __init__(self, items):
        self._events = _FakeCalEvents(items)

    def calendarList(self):
        return _FakeCalendarList()

    def events(self):
        return self._events


def _gmail_message(mid, sender, subject):
    return {
        "id": mid,
        "threadId": "t-" + mid,
        "snippet": subject + " snippet",
        "payload": {"headers": [
            {"name": "From", "value": sender},
            {"name": "Subject", "value": subject},
            {"name": "Date",
             "value": "Mon, 23 Nov 2026 09:00:00 +0530"},
        ]},
    }


class _FakeGmailMessages:
    def __init__(self, messages):
        self._messages = {m["id"]: m for m in messages}
        self._op = None
        self._kwargs = {}

    def list(self, **kwargs):
        self._op, self._kwargs = "list", kwargs
        return self

    def get(self, **kwargs):
        self._op, self._kwargs = "get", kwargs
        return self

    def execute(self):
        if self._op == "list":
            return {"messages": [{"id": mid}
                                 for mid in self._messages]}
        if self._op == "get":
            msg = self._messages.get(self._kwargs.get("id"))
            if msg is None:
                raise Exception("404: message not found")
            return dict(msg)
        raise AssertionError(f"unexpected op {self._op}")


class _FakeGmailUsers:
    def __init__(self, messages):
        self._messages = _FakeGmailMessages(messages)

    def messages(self):
        return self._messages


class _FakeGmailService:
    def __init__(self, messages):
        self._users = _FakeGmailUsers(messages)

    def users(self):
        return self._users


def _audit_totals():
    doc = pathlib.Path(__file__).resolve().parent.parent.joinpath(
        "TOOLS_LOOPHOLES_AUDIT.md").read_text(encoding="utf-8")
    match = re.search(r"<!-- AUDIT-TOTALS: (\{.*?\}) -->", doc)
    return json.loads(match.group(1)), doc


class TestStage0F2Wiring(unittest.TestCase):
    def test_all_four_registered(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in ("find_free_slots", "snooze_email",
                     "cancel_calendar_event",
                     "reschedule_calendar_event"):
            self.assertIn(name, names)

    def test_find_free_slots_is_read_owner_only(self):
        self.assertIn("find_free_slots", _READ_ONLY_TOOLS)
        self.assertIn("find_free_slots", _gate._OWNER_ONLY_READS)
        self.assertNotIn("find_free_slots", _CONFIRM_GATE_TOOLS)
        self.assertNotIn("find_free_slots", _FREE_BY_DESIGN)

    def test_snooze_is_free_app_state(self):
        self.assertEqual(_FREE_BY_DESIGN.get("snooze_email"),
                         "app-state")
        self.assertNotIn("snooze_email", _READ_ONLY_TOOLS)
        self.assertNotIn("snooze_email", _CONFIRM_GATE_TOOLS)

    def test_cancel_and_reschedule_confirm_gated_never_planned(self):
        for name in ("cancel_calendar_event",
                     "reschedule_calendar_event"):
            self.assertIn(name, _CONFIRM_GATE_TOOLS)
            self.assertIn(name, _pa._NEVER_PLAN_KINDS)
            self.assertNotIn(name, _READ_ONLY_TOOLS)
            self.assertNotIn(name, _FREE_BY_DESIGN)

    def test_all_four_resident_desktop(self):
        for name in ("find_free_slots", "snooze_email",
                     "cancel_calendar_event",
                     "reschedule_calendar_event"):
            self.assertIn(name, DESKTOP_TOOL_PRIORITY)

    def test_only_find_free_slots_resident_core(self):
        self.assertIn("find_free_slots",
                      _tokens.CORE_TOOL_PRIORITY)
        for name in ("snooze_email", "cancel_calendar_event",
                     "reschedule_calendar_event"):
            self.assertNotIn(name, _tokens.CORE_TOOL_PRIORITY)

    def test_labels_group_synonyms(self):
        for name in ("find_free_slots", "snooze_email",
                     "cancel_calendar_event",
                     "reschedule_calendar_event"):
            self.assertIn(name, _labels.TOOL_FRIENDLY_NAMES)
            self.assertIn(name, GROUP_MEMBERS["Your world"])
            self.assertIn(name, _tokens.TOOL_SYNONYMS)

    def test_evidence_and_snapshot_entries(self):
        tests_dir = pathlib.Path(__file__).resolve().parent
        ev = json.loads(tests_dir.joinpath(
            "_classification_evidence.json").read_text())
        snap = json.loads(tests_dir.joinpath(
            "_classification_snapshot.json").read_text())
        self.assertEqual(ev["find_free_slots"]["class"], "read")
        self.assertEqual(ev["snooze_email"]["class"], "free")
        self.assertEqual(ev["cancel_calendar_event"]["class"],
                         "confirm")
        self.assertEqual(ev["reschedule_calendar_event"]["class"],
                         "confirm")
        self.assertEqual(snap["find_free_slots"],
                         {"read": True, "confirm": False, "free": None})
        self.assertEqual(snap["snooze_email"],
                         {"read": False, "confirm": False,
                          "free": "app-state"})
        self.assertEqual(snap["cancel_calendar_event"],
                         {"read": False, "confirm": True, "free": None})
        self.assertEqual(snap["reschedule_calendar_event"],
                         {"read": False, "confirm": True, "free": None})

    def test_section_2c_rows_for_the_three_effectful_tools(self):
        _totals, doc = _audit_totals()
        section = doc[doc.index("## 2c."):doc.index("## 3.")]
        rows = re.findall(r"^\| `([a-z0-9_]+)` \|", section, re.M)
        for name in ("snooze_email", "cancel_calendar_event",
                     "reschedule_calendar_event"):
            self.assertIn(name, rows)
        # A read tool takes no §2c row.
        self.assertNotIn("find_free_slots", rows)

    def test_confirm_view_names_the_event(self):
        cancel = _gate._confirmation_args(
            "cancel_calendar_event",
            {"event": "Dentist", "date": "2026-11-23"})
        self.assertIn("Dentist", str(cancel))
        move = _gate._confirmation_args(
            "reschedule_calendar_event",
            {"event": "Dentist", "date": "2026-11-23",
             "new_start": "2026-11-23T17:00:00"})
        self.assertIn("Dentist", str(move))
        self.assertIn("17:00", str(move))

    def test_census_totals(self):
        totals, _doc = _audit_totals()
        self.assertEqual(totals["total"], 772)  #Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(totals["read"], 386)  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(totals["effectful"], 386)  #Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(totals["confirm_gated"], 170)  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(totals["already_verified_in_handler"], 320)  #Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 772)  #Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        params = sum(len(d.get("parameters", {}).get("properties", {}))
                     for d in decls)
        self.assertEqual(params, 2126)  #Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        grouped = sum(len(v) for v in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 769)  #Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        closed = sum(
            1 for d in decls
            if d.get("parameters", {}).get("additionalProperties")
            is False)
        self.assertEqual(closed, 771)  #Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(len(_labels.TOOL_FRIENDLY_NAMES), 697)  #Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170


class _CalBase(unittest.TestCase):
    def setUp(self):
        self._orig_service = _cal._service_or_failure
        self._orig_write_service = _cal._write_service_or_failure

    def tearDown(self):
        _cal._service_or_failure = self._orig_service
        _cal._write_service_or_failure = self._orig_write_service

    def _serve(self, items):
        svc = _FakeCalService(items)
        # Reads go through the read seam; cancel/move go through
        # the WRITE seam (like create_calendar_event).
        _cal._service_or_failure = lambda *a: (svc, None)
        _cal._write_service_or_failure = lambda *a: (svc, None)
        return svc


class TestFindFreeSlots(_CalBase):
    def test_free_blocks_between_events(self):
        self._serve([_event("Standup", 9, 0, 10, 0),
                     _event("Lunch", 12, 0, 13, 0)])
        out = _cal.find_free_slots(date=_DAY, duration_minutes=60)
        self.assertIn("08:00–09:00", out)
        self.assertIn("10:00–12:00", out)
        self.assertIn("13:00–20:00", out)

    def test_duration_filter_skips_short_gaps(self):
        self._serve([_event("Standup", 9, 0, 10, 0),
                     _event("Call", 10, 30, 11, 0)])
        out = _cal.find_free_slots(date=_DAY, duration_minutes=60)
        # The 30-minute 10:00-10:30 gap cannot hold a 60-minute
        # meeting, so it must not appear.
        self.assertIn("08:00–09:00", out)
        self.assertIn("11:00–20:00", out)
        self.assertNotIn("10:00–10:30", out)

    def test_all_day_event_blocks_the_whole_day(self):
        self._serve([_allday("Diwali")])
        out = _cal.find_free_slots(date=_DAY, duration_minutes=60)
        self.assertIn("no free", out.lower())

    def test_timed_event_without_end_reads_as_one_hour(self):
        self._serve([_no_end("Quick call", 9, 0)])
        out = _cal.find_free_slots(date=_DAY, duration_minutes=60)
        self.assertIn("08:00–09:00", out)
        self.assertIn("10:00–20:00", out)

    def test_empty_day_is_the_whole_window(self):
        self._serve([])
        out = _cal.find_free_slots(date=_DAY, duration_minutes=60)
        self.assertIn("08:00–20:00", out)

    def test_failed_fetch_never_claims_a_free_day(self):
        self._serve([])
        orig_fetch = _cal._fetch_events

        def _boom(*args, **kwargs):
            raise RuntimeError("calendar read failed")

        _cal._fetch_events = _boom
        try:
            out = _cal.find_free_slots(date=_DAY)
        finally:
            _cal._fetch_events = orig_fetch
        # A read failure is a plain failure sentence (the read
        # precedent) — it must never read as a free day.
        self.assertIsInstance(out, str)
        self.assertNotIn("Free on", out)
        self.assertIn("Couldn't read", out)

    def test_duration_clamped(self):
        self._serve([])
        out = _cal.find_free_slots(date=_DAY, duration_minutes=1000)
        # 1000 clamps to 480; the empty 08:00-20:00 window holds it.
        self.assertIn("08:00–20:00", out)


class _GmailBase(unittest.TestCase):
    def setUp(self):
        self._orig_service = _gmail._service_or_failure
        self._orig_path = _gmail._snooze_path
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._store = pathlib.Path(self._tmp.name) / "email_snoozed.json"
        _gmail._snooze_path = lambda path=None: self._store

    def tearDown(self):
        _gmail._service_or_failure = self._orig_service
        _gmail._snooze_path = self._orig_path

    def _serve_gmail(self, messages):
        svc = _FakeGmailService(messages)
        _gmail._service_or_failure = lambda *a: (svc, None)
        return svc


class TestSnoozeStore(_GmailBase):
    def test_round_trip_save_load_list_unsnooze(self):
        rows = [{"message_id": "m1", "thread_id": "t1",
                 "subject": "Dentist", "sender": "clinic@example.com",
                 "snoozed_at": "2026-11-22T10:00:00+05:30",
                 "snooze_until": "2999-01-01T09:00:00+05:30"}]
        self.assertTrue(_gmail._save_snoozed(rows, path=self._store))
        loaded = _gmail._load_snoozed(path=self._store)
        self.assertEqual(len(loaded), 1)
        self.assertIn("Dentist", _gmail._list_snoozed(path=self._store))
        self.assertIn("m1", _gmail._active_snooze_ids(path=self._store))
        self.assertTrue(_gmail._unsnooze("m1", path=self._store))
        self.assertEqual(_gmail._load_snoozed(path=self._store), [])

    def test_expired_snooze_is_not_active(self):
        rows = [{"message_id": "m1", "thread_id": "t1",
                 "subject": "Old", "sender": "a@example.com",
                 "snoozed_at": "2026-11-20T10:00:00+05:30",
                 "snooze_until": "2026-11-21T09:00:00+05:30"}]
        _gmail._save_snoozed(rows, path=self._store)
        self.assertNotIn("m1",
                         _gmail._active_snooze_ids(path=self._store))
        self.assertIn("due back",
                      _gmail._list_snoozed(path=self._store))

    def test_parse_snooze_until_forms(self):
        self.assertIsNotNone(_gmail._parse_snooze_until("tomorrow"))
        self.assertIsNotNone(
            _gmail._parse_snooze_until("2026-11-24"))
        self.assertIsNotNone(
            _gmail._parse_snooze_until("2026-11-24T17:30:00"))
        self.assertIsNone(_gmail._parse_snooze_until("next friday"))
        # An empty value means tomorrow 09:00 (the default).
        when = _gmail._parse_snooze_until("")
        self.assertIsNotNone(when)
        tomorrow = (datetime.datetime.now().astimezone()
                    + datetime.timedelta(days=1)).date()
        self.assertEqual(when.date(), tomorrow)
        self.assertEqual((when.hour, when.minute), (9, 0))

    def test_list_mode_empty_store(self):
        self.assertEqual(_gmail.snooze_email(), "No mail snoozed.")


class TestSnoozeHandler(_GmailBase):
    def test_snooze_then_bring_back_early(self):
        self._serve_gmail([_gmail_message(
            "m1", "clinic@example.com", "Dentist")])
        out = _gmail.snooze_email("m1", "tomorrow")
        self.assertIn("Snoozed", out)
        self.assertIn("Dentist", out)
        self.assertIn("m1", _gmail._active_snooze_ids())
        back = _gmail.snooze_email("m1")
        self.assertIn("back in your inbox", back)
        self.assertEqual(_gmail._load_snoozed(), [])

    def test_unparseable_until_is_a_failure_not_a_success(self):
        self._serve_gmail([_gmail_message(
            "m1", "clinic@example.com", "Dentist")])
        out = _gmail.snooze_email("m1", "next friday")
        self.assertEqual(_outcome.outcome_kind(out), "failed")
        self.assertIn("couldn't understand", str(out))

    def test_unknown_message_is_a_failure(self):
        self._serve_gmail([])
        out = _gmail.snooze_email("nope", "tomorrow")
        self.assertEqual(_outcome.outcome_kind(out), "failed")
        self.assertIn("couldn't find", str(out))

    def test_unread_hides_snoozed_and_names_the_count(self):
        self._serve_gmail([
            _gmail_message("m1", "clinic@example.com", "Dentist"),
            _gmail_message("m2", "boss@example.com", "Report")])
        _gmail._save_snoozed([{
            "message_id": "m1", "thread_id": "t-m1",
            "subject": "Dentist", "sender": "clinic@example.com",
            "snoozed_at": "2026-11-22T10:00:00+05:30",
            "snooze_until": "2999-01-01T09:00:00+05:30"}])
        out = _gmail.unread_email()
        self.assertNotIn("Dentist", out)
        self.assertIn("Report", out)
        self.assertIn("1 snoozed", out)

    def test_recent_messages_hides_snoozed(self):
        self._serve_gmail([
            _gmail_message("m1", "clinic@example.com", "Dentist"),
            _gmail_message("m2", "boss@example.com", "Report")])
        _gmail._save_snoozed([{
            "message_id": "m1", "thread_id": "t-m1",
            "subject": "Dentist", "sender": "clinic@example.com",
            "snoozed_at": "2026-11-22T10:00:00+05:30",
            "snooze_until": "2999-01-01T09:00:00+05:30"}])
        rows = _gmail.recent_messages()
        self.assertEqual([r["id"] for r in rows], ["m2"])

    def test_search_still_finds_snoozed_mail(self):
        self._serve_gmail([
            _gmail_message("m1", "clinic@example.com", "Dentist")])
        _gmail._save_snoozed([{
            "message_id": "m1", "thread_id": "t-m1",
            "subject": "Dentist", "sender": "clinic@example.com",
            "snoozed_at": "2026-11-22T10:00:00+05:30",
            "snooze_until": "2999-01-01T09:00:00+05:30"}])
        out = _gmail.search_email("Dentist")
        self.assertIn("Dentist", out)


class TestCancelEvent(_CalBase):
    def test_cancel_by_id_verifies_the_404(self):
        svc = self._serve([_event("Dentist", 9, 0, 10, 0,
                                  event_id="evt-1")])
        out = _cal.cancel_event(event_id="evt-1")
        self.assertIn("Cancelled", str(out))
        self.assertIn("Dentist", str(out))
        self.assertEqual(_outcome.outcome_kind(out), None)
        # The event is really gone from the fake service.
        self.assertNotIn("evt-1", svc.events()._items)

    def test_cancel_by_unique_title(self):
        self._serve([_event("Dentist", 9, 0, 10, 0, event_id="evt-1"),
                     _event("Standup", 11, 0, 11, 30,
                            event_id="evt-2")])
        out = _cal.cancel_event(event="Dentist", date=_DAY)
        self.assertIn("Cancelled", str(out))

    def test_cancel_no_match_is_a_failure(self):
        self._serve([_event("Standup", 9, 0, 10, 0)])
        out = _cal.cancel_event(event="Dentist", date=_DAY)
        self.assertEqual(_outcome.outcome_kind(out), "failed")
        self.assertIn("couldn't find", str(out))

    def test_cancel_ambiguous_title_lists_matches_and_cancels_nothing(
            self):
        svc = self._serve([
            _event("Team sync", 9, 0, 10, 0, event_id="evt-1"),
            _event("Team sync", 14, 0, 15, 0, event_id="evt-2")])
        out = _cal.cancel_event(event="Team sync", date=_DAY)
        self.assertEqual(_outcome.outcome_kind(out), "failed")
        self.assertIn("matches 2 events", str(out))
        self.assertIn("evt-1", svc.events()._items)
        self.assertIn("evt-2", svc.events()._items)

    def test_cancel_with_no_event_and_no_id_is_a_failure(self):
        self._serve([])
        out = _cal.cancel_event()
        self.assertEqual(_outcome.outcome_kind(out), "failed")


class TestRescheduleEvent(_CalBase):
    def test_reschedule_by_id_with_new_end(self):
        svc = self._serve([_event("Dentist", 9, 0, 10, 0,
                                  event_id="evt-1",
                                  desc="Teeth cleaning")])
        out = _cal.reschedule_event(
            event_id="evt-1",
            new_start="2026-11-23T17:00:00",
            new_end="2026-11-23T18:00:00")
        self.assertIn("Moved", str(out))
        self.assertIn("17:00", str(out))
        moved = svc.events()._items["evt-1"]
        self.assertEqual(moved["start"]["dateTime"],
                         _dt(17, 0).isoformat())
        self.assertEqual(moved["end"]["dateTime"],
                         _dt(18, 0).isoformat())
        # The patch wrote start/end only — title and description
        # survive.
        self.assertEqual(moved["summary"], "Dentist")
        self.assertEqual(moved["description"], "Teeth cleaning")

    def test_reschedule_without_new_end_shifts_the_whole_event(self):
        svc = self._serve([_event("Dentist", 9, 0, 10, 0,
                                  event_id="evt-1")])
        out = _cal.reschedule_event(
            event_id="evt-1", new_start="2026-11-23T11:00:00")
        self.assertIn("Moved", str(out))
        moved = svc.events()._items["evt-1"]
        self.assertEqual(moved["start"]["dateTime"],
                         _dt(11, 0).isoformat())
        self.assertEqual(moved["end"]["dateTime"],
                         _dt(12, 0).isoformat())

    def test_reschedule_by_unique_title(self):
        self._serve([_event("Dentist", 9, 0, 10, 0, event_id="evt-1"),
                     _event("Standup", 11, 0, 11, 30,
                            event_id="evt-2")])
        out = _cal.reschedule_event(
            event="Dentist", date=_DAY,
            new_start="2026-11-23T17:00:00")
        self.assertIn("Moved", str(out))

    def test_reschedule_no_match_is_a_failure(self):
        self._serve([_event("Standup", 9, 0, 10, 0)])
        out = _cal.reschedule_event(
            event="Dentist", date=_DAY,
            new_start="2026-11-23T17:00:00")
        self.assertEqual(_outcome.outcome_kind(out), "failed")

    def test_reschedule_ambiguous_title_cancels_nothing(self):
        svc = self._serve([
            _event("Team sync", 9, 0, 10, 0, event_id="evt-1"),
            _event("Team sync", 14, 0, 15, 0, event_id="evt-2")])
        out = _cal.reschedule_event(
            event="Team sync", date=_DAY,
            new_start="2026-11-23T17:00:00")
        self.assertEqual(_outcome.outcome_kind(out), "failed")
        self.assertIn("matches 2 events", str(out))
        self.assertEqual(
            svc.events()._items["evt-1"]["start"]["dateTime"],
            _dt(9, 0).isoformat())

    def test_all_day_event_refuses_a_timed_move(self):
        self._serve([_allday("Diwali", event_id="evt-1")])
        out = _cal.reschedule_event(
            event_id="evt-1", new_start="2026-11-24T17:00:00")
        self.assertEqual(_outcome.outcome_kind(out), "failed")
        self.assertIn("move it by date", str(out))

    def test_all_day_event_moves_by_date(self):
        svc = self._serve([_allday("Diwali", event_id="evt-1")])
        out = _cal.reschedule_event(
            event_id="evt-1", new_start="2026-11-25")
        self.assertIn("Moved", str(out))
        moved = svc.events()._items["evt-1"]
        self.assertEqual(moved["start"], {"date": "2026-11-25"})
        self.assertEqual(moved["end"], {"date": "2026-11-26"})

    def test_reschedule_without_new_start_is_a_failure(self):
        self._serve([_event("Dentist", 9, 0, 10, 0, event_id="evt-1")])
        out = _cal.reschedule_event(event_id="evt-1")
        self.assertEqual(_outcome.outcome_kind(out), "failed")


if __name__ == "__main__":
    unittest.main()
