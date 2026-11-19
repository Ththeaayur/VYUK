"""GAPS-7: connector sync engine (BLOCKS #6).

Before this batch the Gmail/Calendar connectors each hand-rolled a
single-page fetch: one list call capped at 25, no retry, no
pagination, no memory of the last sync. The shared engine
(core/connector_sync.py) pages via pageToken until exhausted,
retries transient failures with exponential backoff, and remembers
a watermark per source so a re-sync fetches only what's new; the
listings run on it, and sync_updates is its user-facing surface.
Stdlib only, no network, no credentials — every service here is a
fake that records its calls.
"""

import datetime
import json
import pathlib
import re
import tempfile
import unittest

from core import connector_sync as cs
from core import google_services_calendar as cal
from core import google_services_gmail as gmail
from core import google_services_sync as syncmod
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
from core.funnel.tools_registry import tools

TREE = pathlib.Path(__file__).resolve().parent.parent


class _Req:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    def execute(self):
        if self.error is not None:
            raise self.error
        return self.response


def _msg_full(i):
    return {
        "payload": {"headers": [
            {"name": "From", "value": "sender%d@example.com" % i},
            {"name": "Subject", "value": "Subject %d" % i},
            {"name": "Date",
             "value": "Thu, 12 Nov 2026 10:00:00 +0000"},
        ]},
        "snippet": "preview %d" % i,
    }


class _FakeGmailMessages:
    """Honours maxResults and pageToken the way the real API does."""

    def __init__(self, total, calls, fail_times=0):
        self.total = total
        self.calls = calls
        self.fail_times = fail_times
        self._fails = 0

    def list(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self._fails < self.fail_times:
            self._fails += 1
            return _Req(error=Exception("503 Backend Error"))
        page_size = kwargs.get("maxResults") or 25
        start = int(kwargs["pageToken"]) if kwargs.get("pageToken") else 0
        end = min(start + page_size, self.total)
        page = {"messages": [{"id": "msg-%d" % i}
                             for i in range(start, end)],
                "resultSizeEstimate": self.total}
        if end < self.total:
            page["nextPageToken"] = str(end)
        return _Req(page)

    def get(self, **kwargs):
        mid = kwargs.get("id") or "msg-0"
        i = int(mid.split("-")[1]) if mid.startswith("msg-") else 0
        return _Req(_msg_full(i))


class _FakeGmail:
    def __init__(self, total, calls, fail_times=0):
        self._messages = _FakeGmailMessages(total, calls, fail_times)

    def users(self):
        return self

    def messages(self):
        return self._messages


def _event(i, when, updated="2026-11-12T10:00:00Z"):
    start = when + datetime.timedelta(minutes=30 * i)
    end = start + datetime.timedelta(minutes=30)
    return {"id": "ev-%d" % i, "summary": "Event %d" % i,
            "updated": updated,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()}}


class _FakeCalendar:
    def __init__(self, events, calls, fail_times=0):
        self.events_list = list(events)
        self.calls = calls
        self.fail_times = fail_times
        self._fails = 0

    def calendarList(self):
        return self

    def events(self):
        return self

    def list(self, **kwargs):
        if "calendarId" not in kwargs:
            return _Req({"items": [{"id": "primary", "primary": True}]})
        self.calls.append(dict(kwargs))
        if self._fails < self.fail_times:
            self._fails += 1
            return _Req(error=Exception("503 Backend Error"))
        page_size = kwargs.get("maxResults") or 25
        start = int(kwargs["pageToken"]) if kwargs.get("pageToken") else 0
        end = min(start + page_size, len(self.events_list))
        page = {"items": self.events_list[start:end]}
        if end < len(self.events_list):
            page["nextPageToken"] = str(end)
        return _Req(page)


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._state_path = pathlib.Path(self._tmp.name) / "state.json"
        self._old_state_path = cs.sync_state_path
        cs.sync_state_path = lambda: self._state_path
        self._old_gmail = gmail._service_or_failure
        self._old_cal = cal._service_or_failure
        self._old_sync = syncmod._service_or_failure

    def tearDown(self):
        cs.sync_state_path = self._old_state_path
        gmail._service_or_failure = self._old_gmail
        cal._service_or_failure = self._old_cal
        syncmod._service_or_failure = self._old_sync
        self._tmp.cleanup()

    def _gmail_service(self, fake):
        gmail._service_or_failure = lambda k, t: (fake, "")
        syncmod._service_or_failure = lambda k, t: (
            (fake, "") if k == "gmail"
            else (None, "Google isn't connected yet."))

    def _calendar_service(self, fake):
        cal._service_or_failure = lambda k, t: (fake, "")
        syncmod._service_or_failure = lambda k, t: (
            (fake, "") if k == "calendar"
            else (None, "Google isn't connected yet."))

    def _events(self, n):
        now = datetime.datetime.now().astimezone()
        return [_event(i, now + datetime.timedelta(hours=1))
                for i in range(n)]


class TransientTests(_Base):
    def test_5xx_and_429_are_transient(self):
        for text in ("503 Backend Error", "500 Internal Server Error",
                     "429 Too Many Requests", "connection reset by peer",
                     "timeout after 30s"):
            self.assertTrue(cs.is_transient_error(Exception(text)), text)

    def test_auth_scope_and_missing_are_not_transient(self):
        for text in ("403 insufficient permissions",
                     "401 invalid credentials", "404 not found",
                     "something broke"):
            self.assertFalse(cs.is_transient_error(Exception(text)), text)

    def test_rate_limit_403_is_transient(self):
        self.assertTrue(cs.is_transient_error(
            Exception("403 rateLimitExceeded")))

    def test_http_status_decides_when_present(self):
        class _Resp:
            status = 503

        class _Err(Exception):
            resp = _Resp()

        self.assertTrue(cs.is_transient_error(_Err("boom")))

        class _Resp403:
            status = 403

        class _Err403(Exception):
            resp = _Resp403()

        self.assertFalse(cs.is_transient_error(_Err403("boom")))


class RetryTests(_Base):
    def test_success_first_try_uses_one_attempt(self):
        value, used = cs.call_with_retry(lambda: 7, sleep=lambda s: None)
        self.assertEqual((value, used), (7, 1))

    def test_retry_then_success_backs_off_exponentially(self):
        delays = []
        state = {"n": 0}

        def flaky():
            state["n"] += 1
            if state["n"] < 3:
                raise Exception("503 Backend Error")
            return "ok"

        value, used = cs.call_with_retry(flaky, sleep=delays.append)
        self.assertEqual((value, used), ("ok", 3))
        self.assertEqual(delays, [0.5, 1.0])

    def test_non_transient_raises_on_the_first_attempt(self):
        calls = []

        def bad():
            calls.append(1)
            raise Exception("403 insufficient permissions")

        with self.assertRaises(Exception):
            cs.call_with_retry(bad, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_exhaustion_raises_the_last_error(self):
        with self.assertRaisesRegex(Exception, "503"):
            cs.call_with_retry(
                lambda: (_ for _ in ()).throw(
                    Exception("503 Backend Error")),
                attempts=3, sleep=lambda s: None)


class PaginateTests(_Base):
    def _pages(self, total, page_size=100):
        def fetch_page(token):
            start = int(token) if token else 0
            end = min(start + page_size, total)
            nxt = str(end) if end < total else None
            return list(range(start, end)), nxt

        return fetch_page

    def test_multi_page_walk_exhausts(self):
        res = cs.paginate(self._pages(250), page_size=100,
                          sleep=lambda s: None)
        self.assertEqual(res["items"], list(range(250)))
        self.assertTrue(res["complete"])
        self.assertEqual(res["pages"], 3)

    def test_limit_truncates_and_is_complete(self):
        res = cs.paginate(self._pages(250), page_size=100, limit=150,
                          sleep=lambda s: None)
        self.assertEqual(len(res["items"]), 150)
        self.assertTrue(res["complete"])
        self.assertEqual(res["pages"], 2)

    def test_page_limit_stops_and_is_incomplete(self):
        res = cs.paginate(self._pages(1000), page_size=100, max_pages=2,
                          sleep=lambda s: None)
        self.assertEqual(len(res["items"]), 200)
        self.assertFalse(res["complete"])
        self.assertEqual(res["stopped"], "page_limit")

    def test_repeated_token_stops_and_is_incomplete(self):
        def looping(token):
            return [1, 2], "same"

        res = cs.paginate(looping, sleep=lambda s: None)
        self.assertEqual(res["items"], [1, 2, 1, 2])
        self.assertFalse(res["complete"])
        self.assertEqual(res["stopped"], "token_repeat")

    def test_page_error_keeps_what_landed_and_is_incomplete(self):
        def fetch_page(token):
            if token is None:
                return [1, 2], "tok"
            raise Exception("503 Backend Error")

        res = cs.paginate(fetch_page, sleep=lambda s: None)
        self.assertEqual(res["items"], [1, 2])
        self.assertFalse(res["complete"])
        self.assertEqual(res["stopped"], "error")
        self.assertIn("503", res["error"])

    def test_empty_walk_is_complete(self):
        res = cs.paginate(lambda token: ([], None),
                          sleep=lambda s: None)
        self.assertEqual(res["items"], [])
        self.assertTrue(res["complete"])
        self.assertEqual(res["pages"], 1)

    def test_describe_partial_names_the_stop(self):
        self.assertEqual(cs.describe_partial({"complete": True}), "")
        self.assertIn("stopped after 2 pages", cs.describe_partial(
            {"complete": False, "stopped": "page_limit", "pages": 2}))
        self.assertIn("same page", cs.describe_partial(
            {"complete": False, "stopped": "token_repeat"}))
        self.assertIn("503", cs.describe_partial(
            {"complete": False, "stopped": "error", "error": "503 boom"}))


class SyncStateTests(_Base):
    def test_put_get_round_trip(self):
        state = cs.SyncState(self._state_path)
        ok, err = state.put("gmail_unread", {"since_date": "2026/11/12",
                                             "seen_ids": ["a", "b"]})
        self.assertTrue(ok, err)
        got = state.get("gmail_unread")
        self.assertEqual(got["since_date"], "2026/11/12")
        self.assertEqual(got["seen_ids"], ["a", "b"])

    def test_a_str_path_round_trips(self):
        # The engine's own seam: sync_state_path() is documented
        # as patchable, and a patched version naturally returns a
        # str (os.path.join). A str path used to break every save
        # silently (load swallows the AttributeError, _save
        # returns False) — the watermark never advanced and the
        # next sync re-fetched everything.
        str_path = str(self._tmp.name) + "/str-state.json"
        st = cs.SyncState(str_path)
        ok, err = st.put("gmail_unread", {"seen_ids": ["a"]})
        self.assertTrue(ok, err)
        self.assertEqual(cs.SyncState(str_path).get("gmail_unread"),
                         {"seen_ids": ["a"]})

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(cs.SyncState(self._state_path).get("x"), {})

    def test_corrupt_file_reads_as_empty(self):
        self._state_path.write_text("{not json", encoding="utf-8")
        self.assertEqual(cs.SyncState(self._state_path).get("x"), {})

    def test_reset_drops_the_key(self):
        state = cs.SyncState(self._state_path)
        state.put("gmail_unread", {"seen_ids": ["a"]})
        ok, _ = state.reset("gmail_unread")
        self.assertTrue(ok)
        self.assertEqual(state.get("gmail_unread"), {})

    def test_save_is_re_read_verified(self):
        state = cs.SyncState(self._state_path)
        state.put("gmail_unread", {"seen_ids": ["a"]})
        saved = json.loads(self._state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["sources"]["gmail_unread"]["seen_ids"],
                         ["a"])


class GmailWiringTests(_Base):
    def test_unread_pages_past_25(self):
        calls = []
        self._gmail_service(_FakeGmail(60, calls))
        out = gmail.unread_email(limit=60)
        self.assertEqual(out.count("• "), 60)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get("maxResults"), 60)
        self.assertNotIn("Couldn't load the rest", out)

    def test_unread_250_pages_in_threes(self):
        calls = []
        self._gmail_service(_FakeGmail(260, calls))
        out = gmail.unread_email(limit=250)
        self.assertEqual(out.count("• "), 250)
        self.assertEqual(len(calls), 3)
        self.assertEqual([c.get("pageToken") for c in calls],
                         [None, "100", "200"])

    def test_retry_recovers_from_transient_failures(self):
        calls = []
        self._gmail_service(_FakeGmail(5, calls, fail_times=2))
        out = gmail.unread_email(limit=8)
        self.assertEqual(out.count("• "), 5)
        self.assertEqual(len(calls), 3)

    def test_non_transient_failure_is_not_retried(self):
        calls = []

        class _Bad(_FakeGmail):
            def messages(self):
                outer = self

                class _M:
                    def list(self, **kw):
                        calls.append(kw)
                        return _Req(error=Exception(
                            "403 insufficient permissions"))

                    def get(self, **kw):
                        return _Req({})

                return _M()

        self._gmail_service(_Bad(0, calls))
        out = gmail.unread_email(limit=8)
        self.assertIn("Couldn't read your mail", out)
        self.assertEqual(len(calls), 1)

    def test_failed_first_page_is_a_failure_not_inbox_zero(self):
        self._gmail_service(_FakeGmail(5, [], fail_times=99))
        out = gmail.unread_email(limit=8)
        self.assertIn("Couldn't read your mail", out)
        self.assertNotIn("inbox zero", out)

    def test_partial_listing_is_disclosed(self):
        calls = []
        self._gmail_service(_FakeGmail(150, calls, fail_times=99))
        # fail_times=99 fails every page: zero items, a failure.
        out = gmail.unread_email(limit=150)
        self.assertIn("Couldn't read your mail", out)

        class _Flaky(_FakeGmail):
            def messages(self):
                base = _FakeGmailMessages(150, calls)

                class _M(_FakeGmailMessages):
                    def list(self, **kw):
                        if kw.get("pageToken") is None:
                            return base.list(**kw)
                        return _Req(error=Exception("503 Backend Error"))

                return _M(150, calls)

        calls.clear()
        self._gmail_service(_Flaky(150, calls))
        out = gmail.unread_email(limit=150)
        self.assertEqual(out.count("• "), 100)
        self.assertIn("Couldn't load the rest", out)
        self.assertIn("503", out)

    def test_search_pages_past_25(self):
        calls = []
        self._gmail_service(_FakeGmail(40, calls))
        out = gmail.search_email("invoice", limit=40)
        self.assertEqual(out.count("• "), 40)
        self.assertEqual(calls[0].get("q"), "invoice")


class CalendarWiringTests(_Base):
    def test_upcoming_pages_past_25(self):
        calls = []
        self._calendar_service(_FakeCalendar(self._events(60), calls))
        out = cal.upcoming_events(days=2, limit=60)
        self.assertEqual(out.count("• "), 60)

    def test_upcoming_250_pages_in_threes(self):
        calls = []
        self._calendar_service(_FakeCalendar(self._events(260), calls))
        out = cal.upcoming_events(days=2, limit=250)
        self.assertEqual(out.count("• "), 250)
        self.assertEqual(len(calls), 3)

    def test_failed_first_page_is_a_failure(self):
        self._calendar_service(_FakeCalendar(self._events(5), [],
                                             fail_times=99))
        out = cal.upcoming_events(days=2, limit=10)
        self.assertIn("Couldn't read your calendar", out)

    def test_partial_calendar_is_disclosed(self):
        events = self._events(150)
        calls = []

        class _Flaky(_FakeCalendar):
            def list(self, **kw):
                if "calendarId" not in kw:
                    return _Req(
                        {"items": [{"id": "primary", "primary": True}]})
                if kw.get("pageToken") is None:
                    return super().list(**kw)
                return _Req(error=Exception("503 Backend Error"))

        self._calendar_service(_Flaky(events, calls))
        out = cal.upcoming_events(days=2, limit=150)
        self.assertEqual(out.count("• "), 100)
        self.assertIn("Couldn't load all events", out)
        self.assertIn("503", out)

    def test_next_events_pages(self):
        calls = []
        # next_events resolves its service through
        # _calendar_service_or_none, not _service_or_failure.
        old = cal._calendar_service_or_none
        fake = _FakeCalendar(self._events(40), calls)
        cal._calendar_service_or_none = lambda: fake
        try:
            got = cal.next_events(within_hours=48, limit=40)
        finally:
            cal._calendar_service_or_none = old
        self.assertEqual(len(got), 40)
        self.assertEqual(got[0]["title"], "Event 0")


class SyncToolTests(_Base):
    def test_first_mail_sync_reports_everything_new(self):
        self._gmail_service(_FakeGmail(3, []))
        out = syncmod.sync_updates(sources="mail")
        self.assertIn("3 new", out)
        self.assertIn("(first sync)", out)

    def test_second_mail_sync_reports_nothing_new(self):
        self._gmail_service(_FakeGmail(3, []))
        syncmod.sync_updates(sources="mail")
        out = syncmod.sync_updates(sources="mail")
        self.assertIn("nothing new", out)

    def test_new_mail_after_a_sync_is_reported_once(self):
        fake = _FakeGmail(3, [])
        self._gmail_service(fake)
        syncmod.sync_updates(sources="mail")
        fake._messages.total = 4
        out = syncmod.sync_updates(sources="mail")
        self.assertIn("1 new", out)
        out = syncmod.sync_updates(sources="mail")
        self.assertIn("nothing new", out)

    def test_full_resync_reports_everything_again(self):
        self._gmail_service(_FakeGmail(2, []))
        syncmod.sync_updates(sources="mail")
        out = syncmod.sync_updates(sources="mail", full=True)
        self.assertIn("2 new", out)

    def test_first_calendar_sync_then_nothing_new(self):
        self._calendar_service(_FakeCalendar(self._events(3), []))
        out = syncmod.sync_updates(sources="calendar")
        self.assertIn("3 new", out)
        out = syncmod.sync_updates(sources="calendar")
        self.assertIn("nothing new", out)

    def test_new_and_updated_events_are_reported(self):
        events = self._events(3)
        fake = _FakeCalendar(events, [])
        self._calendar_service(fake)
        syncmod.sync_updates(sources="calendar")
        fake.events_list.append(_event(3, datetime.datetime.now()
                                       .astimezone()
                                       + datetime.timedelta(hours=1)))
        out = syncmod.sync_updates(sources="calendar")
        self.assertIn("1 new", out)
        self.assertIn("Event 3", out)
        fake.events_list[0] = _event(
            0, datetime.datetime.now().astimezone()
            + datetime.timedelta(hours=1),
            updated="2026-11-13T09:00:00Z")
        out = syncmod.sync_updates(sources="calendar")
        self.assertIn("1 new", out)
        self.assertIn("Event 0", out)

    def test_partial_sync_does_not_advance_the_watermark(self):
        class _Flaky(_FakeGmail):
            def messages(self):
                base = _FakeGmailMessages(150, [])

                class _M(_FakeGmailMessages):
                    def list(self, **kw):
                        if kw.get("pageToken") is None:
                            return base.list(**kw)
                        return _Req(error=Exception("503 Backend Error"))

                return _M(150, [])

        self._gmail_service(_Flaky(150, []))
        out = syncmod.sync_updates(sources="mail")
        self.assertIn("100 new", out)
        self.assertIn("watermark did not advance", out)
        self.assertEqual(cs.SyncState(self._state_path)
                         .get("gmail_unread"), {})
        # The next sync, healthy, reports the whole window as new —
        # the first sync was incomplete, so nothing was remembered.
        self._gmail_service(_FakeGmail(150, []))
        out = syncmod.sync_updates(sources="mail")
        self.assertIn("150 new", out)

    def test_bad_sources_value_is_refused(self):
        out = syncmod.sync_updates(sources="smoke-signal")
        self.assertIn("Sync which", out)

    def test_unconnected_source_reports_its_own_failure(self):
        syncmod._service_or_failure = lambda k, t: (
            None, "Google isn't connected yet.")
        out = syncmod.sync_updates(sources="both")
        self.assertIn("Mail: Google isn't connected", out)
        self.assertIn("Calendar: Google isn't connected", out)

    def test_sync_updates_is_registered_with_two_params(self):
        decls = tools.get_declarations()
        decl = next(d for d in decls if d["name"] == "sync_updates")
        self.assertEqual(sorted(decl["parameters"]["properties"]),
                         ["full", "sources"])
        self.assertEqual(decl["parameters"]["required"], [])
        self.assertIs(decl["parameters"].get("additionalProperties"),
                      False)


class CensusTests(_Base):
    def test_registry_total_is_758(self):  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 758, len(decls))  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertEqual(len({d["name"] for d in decls}), 758)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing

    def test_registry_params_total_is_2079(self):  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; GAPS-20 loopback-meeting pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan
        decls = tools.get_declarations()
        total = sum(len(d["parameters"]["properties"]) for d in decls)
        self.assertEqual(total, 2079, total)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan

    def test_catalogue_grouped_is_754(self):  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 755, grouped)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing

    def test_registry_closed_is_757(self):  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing
        decls = tools.get_declarations()
        closed = sum(1 for d in decls
                     if d["parameters"].get("additionalProperties")
                     is False)
        self.assertEqual(closed, 757, closed)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing

    def test_audit_totals(self):
        text = (TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        for needle in ('"total": 758', '"read": 383',  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing
                       '"effectful": 375', '"confirm_gated": 166',  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 370 -> 371; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374/164 -> 375/165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling
                       '"already_verified_in_handler": 309',  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan
                       '"comms_calendar": 28'):  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): comms_calendar 26 -> 27 (effectful 10 -> 11); round-3 fix Q6 (2026-11-19): comms_calendar 27 -> 28 — one new tool get_morning_briefing
            self.assertIn(needle, text, needle)
        totals = json.loads(re.search(
            r"<!-- AUDIT-TOTALS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(totals["total"], 758)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertEqual(totals["read"], 383)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 382 -> 383 — one new read tool get_morning_briefing
        self.assertEqual(totals["effectful"], 375)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 370 -> 371; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374 -> 375 — one new tool run_plan
        self.assertEqual(totals["confirm_gated"], 166)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling
        self.assertEqual(totals["already_verified_in_handler"], 309)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan
        counts = json.loads(re.search(
            r"<!-- AUDIT-COUNTS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(counts["comms_calendar"], 28)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): comms_calendar 26 -> 27 (effectful 10 -> 11); round-3 fix Q6 (2026-11-19): comms_calendar 27 -> 28 — one new tool get_morning_briefing
        row = next(line for line in text.splitlines()
                   if line.startswith("| comms_calendar |"))
        self.assertIn("| 28 | 11 |", row)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): comms_calendar 26 -> 27 (effectful 10 -> 11); round-3 fix Q6 (2026-11-19): comms_calendar 27 -> 28 — one new tool get_morning_briefing

    def test_confirm_gated_is_164(self):  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed
        self.assertEqual(len(_CONFIRM_GATE_TOOLS), 166)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling


if __name__ == "__main__":
    unittest.main()
