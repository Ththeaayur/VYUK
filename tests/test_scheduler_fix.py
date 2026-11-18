"""Scheduler batch regression suite.

Covers: callbacks outside the lock (deadlock), idempotent
start/stop, aware-UTC normalization, retired briefing dropped
on load + migrated (GAPS-26), catch-up = next future occurrence
(no storm), fired one-shot purge + retention cap, calendar-aware
repeats + DST policy, persistence + missed-event policy. Time-dependent tests use
injectable clocks and direct _tick calls — no real sleeps over
DST boundaries or 30-day waits.

Run from the tree root with PYTHONPATH=. :
    python -m unittest tests.test_scheduler_fix
"""

import datetime
import json
import tempfile
import threading
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from core.scheduler import (
    MAX_HISTORY,
    MISSED_GRACE,
    ProactiveScheduler,
    _next_future_occurrence,
    _resolve_wall_time,
)

UTC = datetime.timezone.utc
NY = ZoneInfo("America/New_York")


def _utc(y, mo, d, h=0, mi=0, s=0):
    return datetime.datetime(y, mo, d, h, mi, s, tzinfo=UTC)


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._schedulers = []

    def tearDown(self):
        for s in self._schedulers:
            try:
                s.stop()
            except Exception:  # noqa: BLE001 — teardown must not mask failures
                pass
        self._tmp.cleanup()

    def sched(self, **kw):
        kw.setdefault("store_path", str(Path(self._tmp.name) / "store.json"))
        s = ProactiveScheduler(**kw)
        self._schedulers.append(s)
        return s


class DeadlockTests(_Base):
    def test_callback_reentrant_read_and_cancel_completes(self):
        """H1/H5: a callback calling back into the scheduler must
        not deadlock. Run the tick in a thread with a timeout so
        a regression fails the test instead of hanging the suite."""
        s = self.sched()
        s.add_reminder("due", _utc(2030, 1, 1, 12, 0), "boom", repeat=False)
        s.add_reminder("other", _utc(2030, 1, 2, 12, 0), "other", repeat=False)
        # Make the first reminder due without waiting for it.
        s._reminders[0].fire_at = _utc(2026, 1, 1, 12, 0)
        seen = []

        def cb(label, message):
            seen.append(label)
            # Re-entrant read AND mutation from inside a callback.
            self.assertTrue(any(r["label"] == "other"
                                for r in s.get_all_reminders()))
            s.cancel_reminder("other")

        s.on_trigger = cb
        done = []
        t = threading.Thread(target=lambda: (s._tick(_utc(2026, 1, 1, 12, 30)),
                                             done.append(True)))
        t.start()
        t.join(timeout=5.0)
        self.assertFalse(t.is_alive(), "tick deadlocked on a re-entrant callback")
        self.assertEqual(done, [True])
        self.assertEqual(seen, ["due"])
        self.assertEqual([r["label"] for r in s.get_all_reminders()], [])

    def test_callback_may_add_reminder(self):
        s = self.sched()
        s.add_reminder("due", _utc(2030, 1, 1, 12, 0), "boom", repeat=False)
        s._reminders[0].fire_at = _utc(2026, 1, 1, 12, 0)

        def cb(label, message):
            s.add_reminder("from-callback", _utc(2030, 1, 3, 12, 0),
                           "added inside a callback", repeat=False)

        s.on_trigger = cb
        done = []
        t = threading.Thread(target=lambda: (s._tick(_utc(2026, 1, 1, 12, 30)),
                                             done.append(True)))
        t.start()
        t.join(timeout=5.0)
        self.assertFalse(t.is_alive(), "tick deadlocked on a re-entrant add")
        self.assertIn("from-callback",
                      [r["label"] for r in s.get_all_reminders()])


class StartStopTests(_Base):
    def test_start_twice_one_thread(self):
        s = self.sched()
        self.assertTrue(s.start())
        first = s._thread
        self.assertTrue(first.is_alive())
        self.assertTrue(s.start())
        self.assertIs(s._thread, first, "second start() spawned a second loop")
        self.assertTrue(s.is_running())

    def test_stop_twice_no_error_and_restart(self):
        s = self.sched()
        s.start()
        s.stop()
        s.stop()  # idempotent: no error
        self.assertFalse(s.is_running())
        self.assertTrue(s.start())
        self.assertTrue(s.is_running())


class AwareDatetimeTests(_Base):
    def test_aware_future_ok_and_converted(self):
        s = self.sched(tz=UTC)
        # 13:30 at +05:30 == 08:00 UTC.
        aware = datetime.datetime(2030, 6, 1, 13, 30,
                                  tzinfo=datetime.timezone(
                                      datetime.timedelta(hours=5, minutes=30)))
        label = s.add_reminder("aware", aware, "hello", repeat=False)
        rec = [r for r in s.get_all_reminders() if r["label"] == label][0]
        self.assertEqual(rec["fire_at"], "2030-06-01 08:00")

    def test_aware_past_raises_value_not_type(self):
        s = self.sched(tz=UTC)
        with self.assertRaises(ValueError):
            s.add_reminder("past", _utc(2020, 1, 1), "nope", repeat=False)

    def test_naive_assumed_scheduler_local(self):
        s = self.sched(tz=NY)
        # Naive 08:00 in New York in January (EST) == 13:00 UTC.
        s.add_reminder("naive", datetime.datetime(2030, 1, 15, 8, 0),
                       "hello", repeat=False)
        self.assertEqual(s._reminders[0].fire_at, _utc(2030, 1, 15, 13, 0))
        self.assertEqual(s.get_all_reminders()[0]["fire_at"], "2030-01-15 08:00")


class BriefingTests(_Base):
    def test_legacy_briefing_record_is_dropped_on_load(self):
        """GAPS-26 (S-V4): the retired scheduler-side briefing can
        never fire again — a kind="briefing" record left in an old
        store is dropped by from_record on load, while ordinary
        reminders load normally."""
        path = str(Path(self._tmp.name) / "store.json")
        payload = {
            "version": 1,
            "reminders": [
                {"label": "morning_briefing",
                 "message": "Good morning. Today is March 11.",
                 "fire_at": "2030-01-01 08:00",
                 "repeat": True, "repeat_kind": "daily",
                 "kind": "briefing",
                 "briefing_hour": 8, "briefing_minute": 0},
                {"label": "once",
                 "message": "once",
                 "fire_at": "2030-01-01 12:00",
                 "repeat": False, "repeat_kind": None,
                 "kind": "reminder"},
            ],
            "fired_history": [], "missed_history": [],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        s = self.sched(store_path=path)
        s._load_and_apply_policy(_utc(2026, 1, 1, 0, 0))
        labels = [r["label"] for r in s.get_all_reminders()]
        self.assertEqual(labels, ["once"])
        self.assertFalse(hasattr(ProactiveScheduler, "add_daily_briefing"))

    def test_migrate_legacy_briefing_records_returns_time_and_removes_record(self):
        """GAPS-26 (S-V4): the one-time migration preserves the
        user's chosen briefing time and rewrites the store without
        the briefing record; a second run finds nothing (None)."""
        from core.scheduler import migrate_legacy_briefing_records
        path = str(Path(self._tmp.name) / "store.json")
        payload = {
            "version": 1,
            "reminders": [
                {"label": "morning_briefing",
                 "message": "Good morning. Today is March 11.",
                 "fire_at": "2030-01-01 08:00",
                 "repeat": True, "repeat_kind": "daily",
                 "kind": "briefing",
                 "briefing_hour": 7, "briefing_minute": 30},
                {"label": "once",
                 "message": "once",
                 "fire_at": "2030-01-01 12:00",
                 "repeat": False, "repeat_kind": None,
                 "kind": "reminder"},
            ],
            "fired_history": [], "missed_history": [],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        self.assertEqual(migrate_legacy_briefing_records(path), (7, 30))
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual([r["label"] for r in payload["reminders"]],
                         ["once"])
        self.assertIsNone(migrate_legacy_briefing_records(path))
        self.assertIsNone(
            migrate_legacy_briefing_records(
                str(Path(self._tmp.name) / "no-such-store.json")))


class CatchupTests(_Base):
    def test_daily_30_days_overdue_fires_once_and_reschedules_future(self):
        """N3: no per-tick storm through the missed month."""
        t0 = _utc(2026, 1, 1, 12, 0)
        s = self.sched(tz=UTC, now_fn=lambda: t0)
        s.add_reminder("daily", _utc(2026, 1, 1, 13, 0), "daily",
                       repeat=True, repeat_kind="daily")
        calls = []
        s.on_trigger = lambda label, msg: calls.append(label)
        s._tick(_utc(2026, 1, 31, 14, 0))
        self.assertEqual(calls, ["daily"])
        # Next future occurrence after 2026-01-31 14:00 is
        # 2026-02-01 13:00 (the Jan 31 13:00 slot already passed).
        self.assertEqual(s._reminders[0].fire_at, _utc(2026, 2, 1, 13, 0))


class PurgeTests(_Base):
    def test_fired_oneshot_purged_from_active_store(self):
        """N4: fired one-shots disappear from get_all_reminders."""
        s = self.sched()
        s.add_reminder("once", _utc(2030, 1, 1, 12, 0), "once", repeat=False)
        s._reminders[0].fire_at = _utc(2026, 1, 1, 12, 0)
        fired = []
        s.on_trigger = lambda label, msg: fired.append(label)
        s._tick(_utc(2026, 1, 1, 12, 30))
        self.assertEqual(fired, ["once"])
        self.assertEqual(s.get_all_reminders(), [])
        hist = s.get_fired_history()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["label"], "once")

    def test_fired_history_retention_cap(self):
        s = self.sched()
        for i in range(MAX_HISTORY + 5):
            s.add_reminder(f"r{i}", _utc(2030, 1, 1, 12, 0), "x", repeat=False)
        for r in s._reminders:
            r.fire_at = _utc(2026, 1, 1, 12, 0)
        s._tick(_utc(2026, 1, 1, 12, 30))
        hist = s.get_fired_history()
        self.assertEqual(len(hist), MAX_HISTORY)
        labels = [h["label"] for h in hist]
        self.assertNotIn("r0", labels, "oldest entries must drop past the cap")
        self.assertIn(f"r{MAX_HISTORY + 4}", labels)


class DstAndCalendarTests(_Base):
    def test_nonexistent_spring_time_shifts_forward_by_gap(self):
        # 2026-03-08 02:30 does not exist in New York (02:00 -> 03:00).
        resolved = _resolve_wall_time(datetime.datetime(2026, 3, 8, 2, 30), NY)
        # 03:30 EDT == 07:30 UTC.
        self.assertEqual(resolved, _utc(2026, 3, 8, 7, 30))

    def test_ambiguous_fall_time_uses_first_occurrence(self):
        # 2026-11-01 01:30 happens twice; fold=0 (EDT) is first.
        resolved = _resolve_wall_time(datetime.datetime(2026, 11, 1, 1, 30), NY)
        self.assertEqual(resolved, _utc(2026, 11, 1, 5, 30))

    def test_daily_repeat_preserves_wall_time_across_spring_forward(self):
        """M5: a daily 08:00 stays 08:00 local after the DST jump —
        absolute +24h arithmetic would give 09:00 EDT instead."""
        s = self.sched(tz=NY, now_fn=lambda: _utc(2026, 3, 6, 12, 0))
        # 2026-03-07 08:00 EST == 13:00 UTC.
        s.add_reminder("daily", _utc(2026, 3, 7, 13, 0), "daily",
                       repeat=True, repeat_kind="daily")
        s._tick(_utc(2026, 3, 8, 12, 0, 30))
        # Next is 2026-03-09 08:00 EDT == 12:00 UTC (wall preserved).
        self.assertEqual(s._reminders[0].fire_at, _utc(2026, 3, 9, 12, 0))
        self.assertEqual(s.get_all_reminders()[0]["fire_at"], "2026-03-09 08:00")

    def test_monthly_clamps_to_month_end(self):
        # Jan 31 -> Feb 28 (2026 is not a leap year); the clamped
        # day then becomes the schedule (Feb 28 -> Mar 28).
        nxt = _next_future_occurrence(_utc(2026, 1, 31, 9, 0), _utc(2026, 2, 15, 0, 0),
                                      "monthly", UTC)
        self.assertEqual(nxt, _utc(2026, 2, 28, 9, 0))
        nxt2 = _next_future_occurrence(_utc(2026, 2, 28, 9, 0), _utc(2026, 3, 15, 0, 0),
                                       "monthly", UTC)
        self.assertEqual(nxt2, _utc(2026, 3, 28, 9, 0))

    def test_weekly_repeat(self):
        nxt = _next_future_occurrence(_utc(2026, 1, 5, 9, 0), _utc(2026, 1, 15, 10, 0),
                                      "weekly", UTC)
        self.assertEqual(nxt, _utc(2026, 1, 19, 9, 0))

    def test_unknown_repeat_kind_rejected(self):
        s = self.sched()
        with self.assertRaises(ValueError):
            s.add_reminder("bad", _utc(2030, 1, 1, 12, 0), "x",
                           repeat=True, repeat_kind="yearly")


class PersistenceTests(_Base):
    def test_roundtrip_restores_reminders_and_briefing(self):
        """M4: reminders survive a scheduler restart via disk.
        GAPS-26 (S-V4): a legacy briefing record in the store is
        NOT restored — it is dropped on load (its time is
        preserved only by migrate_legacy_briefing_records)."""
        path = str(Path(self._tmp.name) / "store.json")
        a = self.sched(store_path=path)
        a.add_reminder("once", _utc(2030, 1, 1, 12, 0), "once", repeat=False)
        a.add_reminder("daily", _utc(2030, 1, 2, 12, 0), "daily",
                       repeat=True, repeat_kind="daily")
        # A briefing record as an older build would have written it.
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(payload["version"], 1)
        payload["reminders"].append({
            "label": "morning_briefing",
            "message": "Good morning. Today is January 1.",
            "fire_at": "2030-01-01 08:00",
            "repeat": True, "repeat_kind": "daily",
            "kind": "briefing",
            "briefing_hour": 8, "briefing_minute": 0})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        self.assertEqual(len(payload["reminders"]), 3)
        b = self.sched(store_path=path)
        b._load_and_apply_policy(_utc(2026, 1, 1, 0, 0))
        labels = sorted(r["label"] for r in b.get_all_reminders())
        self.assertEqual(labels, ["daily", "once"])
        self.assertNotIn("morning_briefing", labels)

    def test_corrupt_store_loads_empty_without_raising(self):
        path = Path(self._tmp.name) / "store.json"
        path.write_text("{not json", encoding="utf-8")
        s = self.sched(store_path=str(path))
        s._load_and_apply_policy(_utc(2026, 1, 1, 0, 0))
        self.assertEqual(s.get_all_reminders(), [])

    def test_load_merges_without_clobbering_in_memory(self):
        path = str(Path(self._tmp.name) / "store.json")
        a = self.sched(store_path=path)
        a.add_reminder("from-disk", _utc(2030, 1, 1, 12, 0), "disk", repeat=False)
        b = self.sched(store_path=path)
        b.add_reminder("in-memory", _utc(2030, 1, 2, 12, 0), "mem", repeat=False)
        b._load_and_apply_policy(_utc(2026, 1, 1, 0, 0))
        labels = sorted(r["label"] for r in b.get_all_reminders())
        self.assertEqual(labels, ["from-disk", "in-memory"])


class MissedPolicyTests(_Base):
    def _stored_oneshot(self, path, fire):
        a = self.sched(store_path=path, now_fn=lambda: _utc(2026, 1, 1, 0, 0))
        a.add_reminder("once", fire, "once", repeat=False)

    def test_missed_within_grace_fires_on_first_tick(self):
        path = str(Path(self._tmp.name) / "store.json")
        self._stored_oneshot(path, _utc(2026, 1, 1, 1, 0))
        b = self.sched(store_path=path)
        b._load_and_apply_policy(_utc(2026, 1, 1, 3, 0))  # 2h late, within grace
        self.assertEqual(len(b.get_all_reminders()), 1)
        fired = []
        b.on_trigger = lambda label, msg: fired.append(label)
        b._tick(_utc(2026, 1, 1, 3, 0))
        self.assertEqual(fired, ["once"])

    def test_missed_beyond_grace_dropped_and_reported(self):
        path = str(Path(self._tmp.name) / "store.json")
        self._stored_oneshot(path, _utc(2026, 1, 1, 1, 0))
        b = self.sched(store_path=path)
        b._load_and_apply_policy(_utc(2026, 1, 2, 3, 0))  # 26h late, past grace
        self.assertEqual(b.get_all_reminders(), [])
        missed = b.get_missed_events()
        self.assertEqual(len(missed), 1)
        self.assertEqual(missed[0]["label"], "once")
        self.assertIn("expired", missed[0]["reason"])

    def test_missed_repeat_rescheduled_without_firing(self):
        path = str(Path(self._tmp.name) / "store.json")
        a = self.sched(store_path=path, now_fn=lambda: _utc(2026, 1, 1, 0, 0))
        a.add_reminder("daily", _utc(2026, 1, 1, 1, 0), "daily",
                       repeat=True, repeat_kind="daily")
        b = self.sched(store_path=path, tz=UTC)
        fired = []
        b.on_trigger = lambda label, msg: fired.append(label)
        b._load_and_apply_policy(_utc(2026, 1, 31, 2, 0))  # 30 days down
        self.assertEqual(fired, [])
        rec = b.get_all_reminders()[0]
        # Next future daily 01:00 after Jan 31 02:00 is Feb 01 01:00.
        self.assertEqual(rec["fire_at"], "2026-02-01 01:00")
        self.assertEqual(b.get_missed_events(), [])


class ShapeTests(_Base):
    def test_get_all_reminders_shape(self):
        s = self.sched()
        s.add_reminder("x", _utc(2030, 1, 1, 12, 0), "x", repeat=False)
        rec = s.get_all_reminders()[0]
        self.assertEqual(set(rec.keys()),
                         {"label", "fire_at", "message", "repeat",
                          "repeat_kind", "kind", "fired", "persistent"})
        self.assertTrue(rec["persistent"])
        self.assertFalse(rec["fired"])


if __name__ == "__main__":
    unittest.main()
