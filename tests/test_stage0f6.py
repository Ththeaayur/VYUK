"""Stage 0 features batch 6 (Group C item 14, 2026-11-23):
calendar-aware quiet — was MISSING (core/daily_quiet.py had
zero calendar mentions; quiet hours were fixed windows only).

This suite pins the batch so it can never regress silently:

- The gates: an in-progress TIMED event blocks can_notify /
  can_speak exactly like a fixed window; outside the event both
  allow; the boundary is start <= now < end (ending exactly at
  now allows, starting exactly at now blocks).
- Timed only: an all-day event never silences (the seam drops
  it AND the cache skips it); a timed event with no parseable
  end never counts; naive datetimes read as local.
- Declined / cancelled never count — dropped by the seam
  (todays_timed_events), proven with a fake calendar service.
- The 10-minute warning still fires at T-10 (it travels the
  suggestions seam, never the quiet gate).
- Alarms stay exempt during a meeting; the fixed window still
  blocks; with no calendar attached the gates behave exactly as
  before; toggle OFF restores the old behaviour.
- The gate never blocks on the network: it answers from a
  cached last-known list; a stale cache refreshes in a daemon
  thread; a failed fetch stores [] (no calendar), never a raise.
- Owner boundary (L-24): the default fetch reads nothing in an
  unproven live-origin session (provenance "auto", unproven);
  with no live session in flight ("unknown") or a proven
  session it fetches.
- Toggle: ConfigSpec calendar_quiet_enabled (default ON, env
  VYUK_CALENDAR_QUIET_ENABLED) + QSettings key
  quiet/calendar_quiet_enabled (default ON) — ConfigSpec 113
  fields / 108 env-backed, QSettings 100 rows. Voice flips it
  through the app_settings allowlist ("calendar quiet" —
  propose -> confirm, §5), writing both stores.
- Settings: the Quiet hours card lives on the Voice page after
  Whisper hours (static pin + bare-mixin handler test; the real
  paint is proven by the by-eye renders in stage0f6_work/).
- Live wiring: w7_wiring_daily.quiet_hours() attaches the
  shared cache, so quiet_gate_for_watchers (the WatcherEngine
  _emit path) and desk_reminder hold during a meeting.
- Census UNMOVED (no new tool): 769 / 384 read / 385 effectful /
  169 confirm / params 2121 / grouped 766 / closed 768 /
  already-verified 319 / §2c 385 / labels 694 / desktop offered 76.
- Settings search: the card is registered in CARD_SYNONYMS
  ("quiet during meetings", "meeting quiet", ...) and
  CARD_VOICE as class B ("turn calendar quiet off") — without
  the voice entry it would fall back to the class-D line
  ("Settings only"), which would be false.

Stdlib unittest only. The gate/seam/config/voice legs are pure
logic and run on both interpreters; the voice set_setting and
Settings-handler legs need Qt (a temp QSettings store) and skip
where PySide6 is absent. Nothing here writes the live tree's
data/ or .env: QuietHours uses a temp store, the .env writes go
to a temp PROJECT_ROOT, the receipts store is pointed at the
temp root, and the shared calendar cache is reset after every
test that touches it. ONE KNOWN EXCEPTION (pre-existing, not
this batch's): the census test imports the full tool registry,
which pulls in core.memory transitively, and importing
core.memory migrates live assistant.db (adds the facts `source`
column — byte-neutral, no rows touched). Restore data/ after
the run, as every leg does.
"""

import dataclasses
import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import daily_quiet as dq  # noqa: E402
from core.config import config as _config  # noqa: E402
from core.config_spec import ConfigSpec  # noqa: E402
from core import config_spec as cs  # noqa: E402

_TREE = Path(__file__).resolve().parent.parent

_TZ = datetime.datetime.now().astimezone().tzinfo


def _qt_available():
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def _install_voice_assistant_alias():
    """Synthesise the voice_assistant package alias (the
    test_chat_ui.WindowBase pattern) with a stub facade
    exposing the real config + qsettings helpers, so gui
    modules that import `voice_assistant.core.gui_facade`
    load in a bare test process. The facade stub carries
    only what window_settings_voice imports at module top;
    the status refresh reaches the wiring through the
    facade (layering rule). Returns the sys.modules keys
    installed, so the caller can remove them afterwards:
    the stub MUST NOT outlive the test — a later suite
    building a real window (test_chat_ui.WindowBase) loads
    the REAL facade through the alias, and a leftover stub
    shadows it (ImportError on the first missing name)."""
    import sys
    import types
    if "voice_assistant" in sys.modules:
        return []
    keys = ["voice_assistant", "voice_assistant.core",
            "voice_assistant.gui",
            "voice_assistant.core.gui_facade"]
    pkg = types.ModuleType("voice_assistant")
    pkg.__path__ = [str(_TREE)]
    sys.modules["voice_assistant"] = pkg
    core_pkg = types.ModuleType("voice_assistant.core")
    core_pkg.__path__ = [str(_TREE / "core")]
    sys.modules["voice_assistant.core"] = core_pkg
    gui_pkg = types.ModuleType("voice_assistant.gui")
    gui_pkg.__path__ = [str(_TREE / "gui")]
    sys.modules["voice_assistant.gui"] = gui_pkg
    facade = types.ModuleType("voice_assistant.core.gui_facade")
    facade.config = _config
    facade.qsettings_get = cs.qsettings_get
    facade.qsettings_set = cs.qsettings_set
    facade.qsettings_contains = cs.qsettings_contains
    facade.qsettings_remove = cs.qsettings_remove
    facade.qsettings_set_noted = cs.qsettings_set_noted
    # The status refresh reaches the wiring through the
    # facade (layering rule) — attach the real module so the
    # handler exercises its production import path.
    import core.w7_wiring_daily as _w7d
    facade.w7_wiring_daily = _w7d
    sys.modules["voice_assistant.core.gui_facade"] = facade
    return keys


def _uninstall_voice_assistant_alias(keys):
    """Remove exactly the alias keys _install_… installed."""
    import sys
    for key in keys:
        sys.modules.pop(key, None)


def _at(hour, minute, day=23, month=11, year=2026):
    return datetime.datetime(year, month, day, hour, minute,
                             tzinfo=_TZ)


def _meeting(start, end, title="Sprint planning"):
    return {"id": "m1", "title": title, "start": start, "end": end,
            "location": None, "all_day": False}


def _allday():
    start = _at(0, 0)
    return {"id": "ad", "title": "Holiday", "start": start,
            "end": start + datetime.timedelta(days=1),
            "location": None, "all_day": True}


class _TempStore(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = str(Path(self._tmp.name) / "daily.json")
        self.cache = dq.CalendarQuietCache()
        self.addCleanup(self.cache.reset)
        # The live config singleton is process-global: any test
        # that flips the toggle restores it.
        self._saved_toggle = bool(
            getattr(_config, "calendar_quiet_enabled", True))
        self.addCleanup(setattr, _config, "calendar_quiet_enabled",
                        self._saved_toggle)

    def _quiet(self, **kwargs):
        kwargs.setdefault("calendar_cache", self.cache)
        kwargs.setdefault("calendar_quiet_enabled", True)
        return dq.QuietHours(self.store, **kwargs)

    def _in_meeting(self, title="Sprint planning"):
        start = _at(10, 0)
        end = _at(11, 0)
        self.cache.set_events([_meeting(start, end, title)])
        return _at(10, 30)


# ------------------------------------------------------------------ gates
class GateTests(_TempStore):
    def test_in_progress_blocks_both_gates(self):
        now = self._in_meeting()
        q = self._quiet()
        self.assertIs(dq.can_notify(q, now), False)
        self.assertIs(dq.can_speak(q, now), False)

    def test_outside_event_both_allow(self):
        self._in_meeting()
        q = self._quiet()
        self.assertIs(dq.can_notify(q, _at(11, 30)), True)
        self.assertIs(dq.can_speak(q, _at(11, 30)), True)
        self.assertIs(dq.can_notify(q, _at(9, 30)), True)

    def test_boundary_end_allows_start_blocks(self):
        self._in_meeting()
        q = self._quiet()
        self.assertIs(dq.can_notify(q, _at(11, 0)), True)
        self.assertIs(dq.can_notify(q, _at(10, 0)), False)

    def test_allday_event_never_silences(self):
        self.cache.set_events([_allday()])
        q = self._quiet()
        self.assertIs(dq.can_notify(q, _at(12, 0)), True)
        self.assertIs(dq.can_speak(q, _at(12, 0)), True)

    def test_event_without_end_never_counts(self):
        ev = _meeting(_at(10, 0), _at(11, 0))
        ev["end"] = None
        self.cache.set_events([ev])
        q = self._quiet()
        self.assertIs(dq.can_notify(q, _at(10, 30)), True)

    def test_naive_datetimes_read_as_local(self):
        ev = {"id": "n1", "title": "Naive meeting",
              "start": datetime.datetime(2026, 11, 23, 10, 0),
              "end": datetime.datetime(2026, 11, 23, 11, 0),
              "location": None, "all_day": False}
        self.cache.set_events([ev])
        q = self._quiet()
        self.assertIs(dq.can_notify(q, _at(10, 30)), False)

    def test_bare_time_now_works(self):
        self._in_meeting()
        q = self._quiet()
        self.assertIs(dq.can_notify(q, datetime.time(10, 30)), False)
        self.assertIs(dq.can_notify(q, datetime.time(12, 0)), True)

    def test_alarms_exempt_during_meeting(self):
        now = self._in_meeting()
        q = self._quiet()
        self.assertIs(dq.can_notify(q, now, is_alarm=True), True)
        self.assertIs(dq.can_speak(q, now, is_alarm=True), True)
        self.assertIs(dq.can_alarm(q, now), True)

    def test_no_calendar_attached_is_old_behaviour(self):
        self._in_meeting()  # the cache exists — but is not attached
        q = dq.QuietHours(self.store)
        self.assertIs(dq.can_notify(q, _at(10, 30)), True)
        self.assertIs(dq.can_speak(q, _at(10, 30)), True)

    def test_toggle_off_restores_old_behaviour(self):
        now = self._in_meeting()
        q = dq.QuietHours(self.store, calendar_cache=self.cache,
                          calendar_quiet_enabled=False)
        self.assertIs(dq.can_notify(q, now), True)
        self.assertIs(dq.can_speak(q, now), True)

    def test_toggle_reads_live_config_when_unset(self):
        now = self._in_meeting()
        q = dq.QuietHours(self.store, calendar_cache=self.cache)
        _config.calendar_quiet_enabled = False
        self.assertIs(dq.can_notify(q, now), True)
        _config.calendar_quiet_enabled = True
        self.assertIs(dq.can_notify(q, now), False)

    def test_fixed_window_still_blocks_and_is_quiet_ignores_calendar(self):
        self._in_meeting()
        q = self._quiet()
        q.set_window("22:00", "07:00")
        self.addCleanup(q.clear_window)
        # is_quiet is the FIXED window only — a meeting at 10:30
        # does not make is_quiet true.
        self.assertIs(q.is_quiet(_at(10, 30)), False)
        self.assertIs(q.is_quiet(_at(23, 0)), True)
        self.assertIs(dq.can_notify(q, _at(23, 0)), False)
        # ...and the meeting still blocks at 10:30 via the gate.
        self.assertIs(dq.can_notify(q, _at(10, 30)), False)

    def test_gate_reasons(self):
        now = self._in_meeting()
        q = self._quiet()
        ok, reason = dq.gate_notify(q, now)
        self.assertIs(ok, False)
        self.assertIn("Sprint planning", reason)
        self.assertIn("11:00", reason)
        ok, reason = dq.gate_speak(q, now)
        self.assertIs(ok, False)
        self.assertIn("Sprint planning", reason)
        # The fixed-window refusal is unchanged.
        q2 = dq.QuietHours(self.store)
        q2.set_window("22:00", "07:00")
        self.addCleanup(q2.clear_window)
        ok, reason = dq.gate_notify(q2, _at(23, 0))
        self.assertIs(ok, False)
        self.assertEqual(
            reason,
            "quiet hours are on — notifications are held until "
            "quiet hours end, except for alarms.")

    def test_fake_quiet_without_calendar_methods_is_old_behaviour(self):
        class _OldQuiet:
            def is_quiet(self, now=None):
                return False

        self.assertIs(dq.can_notify(_OldQuiet(), _at(10, 30)), True)
        self.assertIs(dq.can_speak(_OldQuiet(), _at(10, 30)), True)


# ------------------------------------------------------------------ cache
class CacheTests(_TempStore):
    def test_refresh_stores_fetch_and_failed_fetch_stores_empty(self):
        def good():
            return [_meeting(_at(10, 0), _at(11, 0))]

        self.cache.refresh(fetch_fn=good)
        self.assertIs(self.cache.is_in_meeting(_at(10, 30)), True)

        def bad():
            raise RuntimeError("network down")

        self.cache.refresh(fetch_fn=bad)
        self.assertIs(self.cache.is_in_meeting(_at(10, 30)), False)

    def test_refresh_async_answers_from_cache_and_never_blocks(self):
        calls = []

        def slow_fetch():
            calls.append(1)
            return [_meeting(_at(10, 0), _at(11, 0))]

        # A fresh cache with a TTL of 0 refreshes on consult...
        cache = dq.CalendarQuietCache(ttl_seconds=0)
        self.addCleanup(cache.reset)
        cache.refresh_async(fetch_fn=slow_fetch)
        # ...but the consult itself answers at once (False — the
        # background fetch has not landed yet is also a legal
        # answer here; the point is it returns, not what it says).
        self.assertIsInstance(cache.is_in_meeting(_at(10, 30)), bool)
        # ...and the background refresh lands the events.
        import time as _time
        for _ in range(100):
            if calls:
                break
            _time.sleep(0.05)
        self.assertEqual(len(calls), 1)
        self.assertIs(cache.is_in_meeting(_at(10, 30)), True)

    def test_current_event_returns_the_meeting(self):
        self._in_meeting()
        ev = self.cache.current_event(_at(10, 30))
        self.assertEqual(ev["title"], "Sprint planning")
        self.assertIsNone(self.cache.current_event(_at(12, 0)))

    def test_set_events_counts_as_fresh_fetch(self):
        # set_events stamps fetched_at: no background refresh
        # fires within the TTL.
        calls = []
        self.cache.set_events([_meeting(_at(10, 0), _at(11, 0))])
        self.cache.refresh_async(fetch_fn=lambda: calls.append(1))
        self.assertEqual(calls, [])

    def test_default_fetch_reads_nothing_in_unproven_live_session(self):
        import core.funnel.tools_gate as tg
        import core.google_services_calendar as gsc

        prev_prov = tg.session_provenance()
        prev_proven = tg.session_owner_proven()

        def _restore():
            tg.set_session_provenance(prev_prov)
            if prev_proven:
                tg.mark_session_owner_proven("user-started")
            else:
                tg.mark_session_owner_unproven()

        self.addCleanup(_restore)
        called = []
        fake_meeting = [_meeting(_at(10, 0), _at(11, 0))]
        with mock.patch.object(
                gsc, "todays_timed_events",
                side_effect=lambda: (called.append(1),
                                     fake_meeting)[1]):
            tg.set_session_provenance("auto")  # unproven live session
            self.assertEqual(dq._default_calendar_fetch(), [])
            self.assertEqual(called, [])
            tg.set_session_provenance("unknown")  # no live session
            self.assertEqual(dq._default_calendar_fetch(), fake_meeting)
            self.assertEqual(called, [1])


# ------------------------------------------------------------------ fake service
class _FakeList:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeResource:
    def __init__(self, payload):
        self._payload = payload

    def list(self, **kwargs):
        return _FakeList(self._payload)


class _FakeCalendarService:
    def __init__(self, events):
        self._events = events

    def calendarList(self):
        return _FakeResource(
            {"items": [{"id": "primary", "primary": True}]})

    def events(self):
        return _FakeResource({"items": self._events})


def _raw_event(summary, start, end, status="confirmed",
               response="accepted", all_day=False):
    if all_day:
        day = start.date().isoformat()
        nxt = (start.date() + datetime.timedelta(days=1)).isoformat()
        return {"id": f"ev-{summary}", "summary": summary,
                "start": {"date": day}, "end": {"date": nxt},
                "status": status}
    ev = {"id": f"ev-{summary}", "summary": summary,
          "start": {"dateTime": start.isoformat()},
          "status": status}
    if end is not None:
        ev["end"] = {"dateTime": end.isoformat()}
    if response is not None:
        ev["attendees"] = [{"self": True,
                            "responseStatus": response}]
    return ev


def _today_at(hour, minute):
    now = datetime.datetime.now().astimezone()
    return now.replace(hour=hour, minute=minute, second=0,
                       microsecond=0)


# ------------------------------------------------------------------ seam
class SeamTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        import core.google_services_calendar as gsc
        self.gsc = gsc
        self._orig = gsc._calendar_service_or_none
        self.addCleanup(setattr, gsc, "_calendar_service_or_none",
                        self._orig)

    def _run(self, events):
        self.gsc._calendar_service_or_none = (
            lambda: _FakeCalendarService(events))
        return self.gsc.todays_timed_events()

    def test_keeps_timed_with_end_drops_the_rest(self):
        events = [
            _raw_event("Timed kept", _today_at(10, 0), _today_at(11, 0)),
            _raw_event("Declined", _today_at(12, 0), _today_at(13, 0),
                       response="declined"),
            _raw_event("Cancelled", _today_at(14, 0), _today_at(15, 0),
                       status="cancelled"),
            _raw_event("All day", _today_at(0, 0), None, all_day=True),
            _raw_event("No end", _today_at(16, 0), None),
            _raw_event("Tentative kept", _today_at(17, 0),
                       _today_at(18, 0), response="tentative"),
        ]
        got = self._run(events)
        titles = [e["title"] for e in got]
        self.assertEqual(titles, ["Timed kept", "Tentative kept"])
        kept = got[0]
        self.assertEqual(kept["start"], _today_at(10, 0))
        self.assertEqual(kept["end"], _today_at(11, 0))
        self.assertIs(kept["all_day"], False)

    def test_empty_when_not_connected(self):
        self.gsc._calendar_service_or_none = lambda: None
        self.assertEqual(self.gsc.todays_timed_events(), [])

    def test_failed_read_degrades_to_empty(self):
        class _Boom:
            def calendarList(self):
                raise RuntimeError("Google is down")

        self.gsc._calendar_service_or_none = lambda: _Boom()
        self.assertEqual(self.gsc.todays_timed_events(), [])

    def test_facade_reexports_the_accessor(self):
        import core.google_services as gs
        self.assertIs(gs.todays_timed_events,
                      self.gsc.todays_timed_events)


# ------------------------------------------------------------------ warning
class WarningTests(unittest.TestCase):
    def test_warning_still_fires_at_T_minus_10(self):
        from core.brain import suggestions as sugg
        now = datetime.datetime.now().astimezone()
        start = now + datetime.timedelta(minutes=10)
        ev = {"id": "w1", "title": "Sprint planning",
              "start": start, "location": None, "all_day": False}
        registry = sugg.SuggestionRegistry(now_fn=lambda: now)
        rec = sugg.poll_meeting_suggestions([ev], now=now,
                                            registry=registry)
        self.assertIsNotNone(rec)
        self.assertIn("Sprint planning", rec["text"])
        self.assertIn(start.strftime("%H:%M"), rec["text"])

    def test_warning_does_not_fire_once_started(self):
        from core.brain import suggestions as sugg
        now = datetime.datetime.now().astimezone()
        start = now - datetime.timedelta(minutes=5)
        ev = {"id": "w2", "title": "Already started",
              "start": start, "location": None, "all_day": False}
        registry = sugg.SuggestionRegistry(now_fn=lambda: now)
        rec = sugg.poll_meeting_suggestions([ev], now=now,
                                            registry=registry)
        self.assertIsNone(rec)


# ------------------------------------------------------------------ config
class ConfigTests(unittest.TestCase):
    def test_facade_exports_w7_wiring_daily(self):
        """The Settings status line reaches the wiring through
        the facade (layering rule, test_gaps30 T-V8) — the
        export must exist in the table and in __all__, and
        resolve to the identical module object."""
        import core.gui_facade as fac
        import core.w7_wiring_daily as w7d
        self.assertIn("w7_wiring_daily", fac._EXPORTS)
        self.assertIn("w7_wiring_daily", fac.__all__)
        self.assertIs(fac.w7_wiring_daily, w7d)

    def test_field_exists_default_on_env_name(self):
        fields = {f.name: f for f in dataclasses.fields(ConfigSpec)}
        field = fields["calendar_quiet_enabled"]
        self.assertIs(field.metadata.get("env"),
                      "VYUK_CALENDAR_QUIET_ENABLED")
        self.assertIs(ConfigSpec().calendar_quiet_enabled, True)

    def test_config_spec_counts(self):
        fields = dataclasses.fields(ConfigSpec)
        env_backed = [f for f in fields if f.metadata.get("env")]
        self.assertEqual(len(fields), 113)
        self.assertEqual(len(env_backed), 108)

    def test_qsettings_row(self):
        row = next((e for e in cs.QSETTINGS_FIELDS
                    if e[0] == "calendar_quiet_enabled"), None)
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "quiet/calendar_quiet_enabled")
        self.assertIs(row[2], bool)
        self.assertIs(row[3], True)
        self.assertEqual(len(cs.QSETTINGS_FIELDS), 100)

    def test_toggle_reader(self):
        self.assertIs(dq.calendar_quiet_enabled(), True)
        _config.calendar_quiet_enabled = False
        try:
            self.assertIs(dq.calendar_quiet_enabled(), False)
        finally:
            _config.calendar_quiet_enabled = True

    def test_daily_reexports(self):
        import core.daily as d
        for name in ("CalendarQuietCache", "calendar_quiet_enabled",
                     "calendar_quiet_cache",
                     "reset_calendar_quiet_cache"):
            self.assertIn(name, d.__all__)
            self.assertTrue(hasattr(d, name), name)


# ------------------------------------------------------------------ search
class SearchTests(unittest.TestCase):
    def test_card_is_findable_and_voice_flippable(self):
        """The card must be findable by plain words AND carry
        the right voice class: unlisted cards fall back to
        class D ("Settings only"), which would be false here
        — voice flips calendar quiet through the §5 protocol."""
        from core import settings_search as search
        self.assertIn("Quiet hours", search.CARD_SYNONYMS)
        titles = list(search.CARD_SYNONYMS)
        self.assertIn("Quiet hours",
                      search.match_cards("quiet during meetings",
                                         titles))
        self.assertIn("Quiet hours",
                      search.match_cards("meeting quiet", titles))
        self.assertEqual(search.card_class("Quiet hours"), "B")
        self.assertIn("calendar quiet",
                      search.card_voice_line("Quiet hours"))


# ------------------------------------------------------------------ voice
@unittest.skipUnless(_qt_available(), "Qt only: PySide6 unavailable")
class VoiceTests(unittest.TestCase):
    """The voice path needs Qt (a temp QSettings store) and a
    temp PROJECT_ROOT (the .env half must never land in the
    live tree)."""

    def setUp(self):
        super().setUp()
        from PySide6.QtCore import QSettings
        import core.app_settings as aps
        import core.config as config_mod
        import core.config_env as config_env_mod
        import core.settings_voice as sv

        self.aps = aps
        self.sv = sv
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = QSettings(
            str(Path(self._tmp.name) / "test.ini"),
            QSettings.IniFormat)
        self._orig_qs = aps._qsettings
        aps._qsettings = lambda: self.store
        self.addCleanup(setattr, aps, "_qsettings", self._orig_qs)
        self._root = Path(self._tmp.name) / "root"
        self._root.mkdir()
        self._orig_proj = config_mod.PROJECT_ROOT
        self._orig_env_proj = config_env_mod.PROJECT_ROOT
        config_mod.PROJECT_ROOT = self._root
        config_env_mod.PROJECT_ROOT = self._root
        self.addCleanup(setattr, config_mod, "PROJECT_ROOT",
                        self._orig_proj)
        self.addCleanup(setattr, config_env_mod, "PROJECT_ROOT",
                        self._orig_env_proj)
        self._saved_env = os.environ.pop(
            "VYUK_CALENDAR_QUIET_ENABLED", None)
        self.addCleanup(self._restore_env)
        self._saved_toggle = bool(
            getattr(_config, "calendar_quiet_enabled", True))
        self.addCleanup(setattr, _config, "calendar_quiet_enabled",
                        self._saved_toggle)
        sv.reset_for_tests()
        self.addCleanup(sv.reset_for_tests)

        # Receipts: propose/confirm/undo record a tool receipt
        # through the module-global store, which defaults to
        # data/tool_receipts.jsonl. Point it at the temp root so
        # the receipts stay REAL (written + readable) without
        # dirtying live data; restore the global afterwards.
        import core.tool_receipts as _tr
        from core.tool_receipts_store import ReceiptStore
        self._saved_receipt_store = _tr._default_store
        self._saved_receipt_disabled = _tr._store_disabled
        _tr._default_store = ReceiptStore(
            self._root / "tool_receipts.jsonl")
        _tr._store_disabled = False
        self.addCleanup(self._restore_receipt_store)

    def _restore_receipt_store(self):
        import core.tool_receipts as _tr
        _tr._default_store = self._saved_receipt_store
        _tr._store_disabled = self._saved_receipt_disabled

    def _restore_env(self):
        if self._saved_env is None:
            os.environ.pop("VYUK_CALENDAR_QUIET_ENABLED", None)
        else:
            os.environ["VYUK_CALENDAR_QUIET_ENABLED"] = self._saved_env

    def test_set_off_then_on_writes_both_stores(self):
        out = self.aps.set_setting("calendar_quiet", "off")
        self.assertIn("turned off", out)
        self.assertEqual(self.aps._current("calendar_quiet"), "off")
        self.assertIs(_config.calendar_quiet_enabled, False)
        from core.config_spec import qsettings_get
        self.assertIs(
            qsettings_get("quiet/calendar_quiet_enabled",
                          settings=self.store), False)
        env_file = self._root / ".env"
        self.assertTrue(env_file.exists())
        self.assertIn("VYUK_CALENDAR_QUIET_ENABLED=false",
                      env_file.read_text(encoding="utf-8"))
        out = self.aps.set_setting("calendar_quiet", "on")
        self.assertIn("turned on", out)
        self.assertEqual(self.aps._current("calendar_quiet"), "on")
        self.assertIs(_config.calendar_quiet_enabled, True)

    def test_propose_confirm_undo(self):
        proposal, message = self.sv.propose_setting_change(
            "calendar quiet", "off")
        self.assertIsNotNone(proposal)
        self.assertIn("Calendar quiet", message)
        announced = self.sv.confirm_proposal(proposal["id"])
        self.assertIn("now off", announced)
        self.assertEqual(self.aps._current("calendar_quiet"), "off")
        undone = self.sv.undo_last_change()
        self.assertIn("back to on", undone)
        self.assertEqual(self.aps._current("calendar_quiet"), "on")

    def test_aliases_and_bad_value(self):
        self.assertEqual(self.aps._canonical_key("meeting quiet"),
                         "calendar_quiet")
        self.assertEqual(
            self.aps._canonical_key("quiet during meetings"),
            "calendar_quiet")
        out = self.aps.set_setting("calendar_quiet", "maybe")
        self.assertIn("tell me 'on' or 'off'", out)
        self.assertEqual(self.aps._current("calendar_quiet"), "on")

    def test_get_lists_the_setting(self):
        out = self.aps.get_settings("calendar quiet")
        self.assertIn("Calendar quiet", out)
        self.assertIn("on", out)


# ------------------------------------------------------------------ wiring
class WiringTests(_TempStore):
    def setUp(self):
        super().setUp()
        self.addCleanup(dq.reset_calendar_quiet_cache)

    def test_wiring_attaches_shared_cache(self):
        import core.w7_wiring_daily as w7d
        q = w7d.quiet_hours(store_path=self.store)
        self.assertIs(q._calendar_cache, dq.calendar_quiet_cache())

    def test_watcher_gate_holds_during_meeting(self):
        import core.w7_wiring_daily as w7d
        now = self._in_meeting()
        dq.calendar_quiet_cache().set_events(
            [_meeting(_at(10, 0), _at(11, 0))])
        allowed, reason = w7d.quiet_gate_for_watchers(
            now, store_path=self.store)
        self.assertIs(allowed, False)
        self.assertIn("Sprint planning", reason)
        dq.reset_calendar_quiet_cache()
        allowed, _reason = w7d.quiet_gate_for_watchers(
            now, store_path=self.store)
        self.assertIs(allowed, True)

    def test_desk_reminder_held_during_meeting(self):
        from core.health.health import desk_reminder
        now = self._in_meeting()
        q = self._quiet()
        held = desk_reminder(quiet_hours=q, now=now)
        self.assertEqual(held, {"ok": False, "held": True,
                                "note": "Reminder held during "
                                        "quiet hours."})
        free = desk_reminder(quiet_hours=q, now=_at(12, 0))
        self.assertIs(free["ok"], True)
        self.assertIs(free["held"], False)


# ------------------------------------------------------------------ settings UI
@unittest.skipUnless(_qt_available(), "Qt only: PySide6 unavailable")
class SettingsUiTests(unittest.TestCase):
    def test_card_exists_in_source(self):
        text = (_TREE / "gui" / "window_settings_voice.py"
                ).read_text(encoding="utf-8")
        self.assertIn('"Quiet hours"', text)
        self.assertIn("chk_calendar_quiet", text)
        self.assertIn('"Go quiet during meetings"', text)
        self.assertIn("_on_calendar_quiet_toggled", text)
        self.assertIn("_refresh_calendar_quiet_status", text)

    def test_toggle_handler_flips_both_stores(self):
        from PySide6.QtCore import QSettings
        import core.config as config_mod
        import core.config_env as config_env_mod
        from core.config_spec import qsettings_get
        installed = _install_voice_assistant_alias()
        self.addCleanup(_uninstall_voice_assistant_alias,
                        installed)
        from gui.window_settings_voice import SettingsVoiceMixin

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = QSettings(str(Path(tmp.name) / "test.ini"),
                          QSettings.IniFormat)
        root = Path(tmp.name) / "root"
        root.mkdir()
        orig_proj = config_mod.PROJECT_ROOT
        orig_env_proj = config_env_mod.PROJECT_ROOT
        config_mod.PROJECT_ROOT = root
        config_env_mod.PROJECT_ROOT = root
        self.addCleanup(setattr, config_mod, "PROJECT_ROOT",
                        orig_proj)
        self.addCleanup(setattr, config_env_mod, "PROJECT_ROOT",
                        orig_env_proj)
        saved_env = os.environ.pop(
            "VYUK_CALENDAR_QUIET_ENABLED", None)

        def _restore_env():
            if saved_env is None:
                os.environ.pop("VYUK_CALENDAR_QUIET_ENABLED", None)
            else:
                os.environ["VYUK_CALENDAR_QUIET_ENABLED"] = saved_env

        self.addCleanup(_restore_env)
        saved_toggle = bool(
            getattr(_config, "calendar_quiet_enabled", True))
        self.addCleanup(setattr, _config, "calendar_quiet_enabled",
                        saved_toggle)

        class _Label:
            def __init__(self):
                self.text = ""

            def setText(self, value):
                self.text = value

        mixin = SettingsVoiceMixin()
        mixin.settings = store
        mixin._calendar_quiet_status = _Label()
        mixin._on_calendar_quiet_toggled(False)
        self.assertIs(_config.calendar_quiet_enabled, False)
        self.assertIs(qsettings_get("quiet/calendar_quiet_enabled",
                                    settings=store), False)
        self.assertIn("quiet window",
                      mixin._calendar_quiet_status.text.lower())
        mixin._on_calendar_quiet_toggled(True)
        self.assertIs(_config.calendar_quiet_enabled, True)
        self.assertIs(qsettings_get("quiet/calendar_quiet_enabled",
                                    settings=store), True)


# ------------------------------------------------------------------ census
class CensusTests(unittest.TestCase):
    def test_census_unmoved(self):
        # No new tool in this batch: the census is the batch-5
        # parent-verified 769 / 2121 / 384 / 385 / 169. The
        # counts are re-derived by the worker on a scratch copy
        # (r3f_census.json) and asserted here from the registry,
        # so a silent tool addition fails loudly.
        from core.funnel.tools_registry import tools
        from core.funnel.tool_classification import (
            _READ_ONLY_TOOLS as READ,
            _CONFIRM_GATE_TOOLS as CONFIRM,
        )
        decls = tools.get_declarations()
        params = sum(
            len((d.get("parameters") or {}).get("properties") or {})
            for d in decls)
        self.assertEqual(len(decls), 769)
        self.assertEqual(params, 2121)
        self.assertEqual(len([d for d in decls
                              if d["name"] in READ]), 384)
        self.assertEqual(len([d for d in decls
                              if d["name"] not in READ]), 385)
        self.assertEqual(len([d for d in decls
                              if d["name"] in CONFIRM]), 169)


if __name__ == "__main__":
    unittest.main()
