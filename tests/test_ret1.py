"""RET-1 (forgiving daily count + ONE daily nudge) — pins the
behaviour. BUILD items pin the new module; the batch prover
(prove_ret1.py) re-runs the same shapes plus D1/D2; the
neighbour tally is the backstop. Stdlib only; every probe
runs against temp dirs, so the suite never touches the live
data/ and never speaks. Subprocess legs use the current
interpreter so the suite passes on both interpreters.
"""

import datetime
import json
import re
import tempfile
import threading
import unittest
from pathlib import Path

TREE_ROOT = Path(__file__).resolve().parent.parent

from core import daily_count as dc  # noqa: E402
from core import voice_origin  # noqa: E402
from core.scheduled_agents import (  # noqa: E402
    run_digest, run_job, validate_manifest)


def _read(rel: str) -> str:
    return (TREE_ROOT / rel).read_text(encoding="utf-8")


def _marker(day, hour=8):
    return "2026-11-%02dT%02d:00:00 | voice_task | success" % (day, hour)


def _no_sources():
    return ([], None)


def _no_routines():
    return ([], None)


def _no_events():
    return ([], True, "")


class DailyCountTests(unittest.TestCase):
    # -- the count is derived from the activity log ----------------
    def test_week_counts_missed_day_unchanged_then_monday_rollover(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "activity.log"
            # Mon, Tue, Thu, Fri used; Wed missed (no marker).
            log.write_text("\n".join([
                _marker(16), _marker(17), _marker(19), _marker(20),
                "2026-11-18T08:00:00 | get_current_time_date | success",
            ]) + "\n", encoding="utf-8")
            counts = dc.week_counts(
                log, datetime.datetime(2026, 11, 22, 9, 0))
            self.assertEqual(counts["this_week"], 4)
            self.assertEqual(counts["last_week"], 0)
            # Monday of the next week: the week rolls over to 0/7.
            rolled = dc.week_counts(
                log, datetime.datetime(2026, 11, 23, 9, 0))
            self.assertEqual(rolled["this_week"], 0)
            self.assertEqual(rolled["last_week"], 4)

    def test_monday_display_always_shows_last_week(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "activity.log"
            log.write_text("\n".join([_marker(16), _marker(17),
                                      _marker(18)]) + "\n", encoding="utf-8")
            counts = dc.week_counts(
                log, datetime.datetime(2026, 11, 23, 9, 0))
            self.assertEqual(
                counts["display"],
                "Days used this week: 0/7 \u00b7 last week: 3/7")

    def test_no_voice_marker_no_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "activity.log"
            # App opened, tools ran — but no voice-driven success.
            log.write_text("\n".join([
                "2026-11-16T08:00:00 | get_current_time_date | success",
                "2026-11-17T08:00:00 | list_files | success",
            ]) + "\n", encoding="utf-8")
            counts = dc.week_counts(
                log, datetime.datetime(2026, 11, 22, 9, 0))
            self.assertEqual(counts["this_week"], 0)

    def test_no_reset_to_zero_function(self):
        text = _read("core/daily_count.py")
        self.assertNotRegex(text, r"def\s+\w*reset\w*")
        self.assertNotIn("reset_to_zero", text)
        self.assertNotIn("zero_the_week", text)


class VoiceOriginTests(unittest.TestCase):
    # -- the voice-turn flag (B-106) -------------------------------
    def test_voice_turn_marks_and_clears(self):
        self.assertFalse(voice_origin.voice_turn_active())
        with voice_origin.voice_turn():
            self.assertTrue(voice_origin.voice_turn_active())
        self.assertFalse(voice_origin.voice_turn_active())

    def test_voice_turn_does_not_leak_into_new_threads(self):
        seen = []
        with voice_origin.voice_turn():
            t = threading.Thread(
                target=lambda: seen.append(
                    voice_origin.voice_turn_active()))
            t.start()
            t.join()
        self.assertEqual(seen, [False])

    def test_funnel_hook_writes_marker_on_voice_success(self):
        text = _read("core/funnel/tools_registry_helpers.py")
        self.assertIn("voice_task", text)
        self.assertIn("voice_turn_active", text)


class NudgeTests(unittest.TestCase):
    def _store(self, tmp):
        return dc.DailyCountStore(Path(tmp) / "daily_count.json")

    def _fire(self, store, now, speak, **kw):
        return dc.fire_nudge(store, now=now, speak_fn=speak,
                             reminders_fn=_no_sources,
                             routines_fn=_no_routines,
                             calendar_fn=_no_events, **kw)

    # -- one nudge a day (B-102) ------------------------------------
    def test_double_fire_speaks_once(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "activity.log"
            log.write_text(_marker(16) + "\n", encoding="utf-8")
            store = self._store(tmp)
            dc.accept_offer(store, "07:00")
            first = self._fire(store, datetime.datetime(2026, 11, 16, 7, 30),
                               lambda text: calls.append(text) or {"ok": True},
                               activity_path=log)
            second = self._fire(store, datetime.datetime(2026, 11, 16, 7, 45),
                                lambda text: calls.append(text) or {"ok": True},
                                activity_path=log)
            self.assertTrue(first["ok"])
            self.assertEqual(first["outcome"], "spoken")
            self.assertFalse(second["ok"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(store.read()["last_nudge_date"], "2026-11-16")

    def test_speak_failure_consumes_the_day(self):
        def dead_speak(text):
            raise RuntimeError("speaker dead")

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "activity.log"
            log.write_text(_marker(16) + "\n", encoding="utf-8")
            store = self._store(tmp)
            dc.accept_offer(store, "07:00")
            failed = self._fire(store, datetime.datetime(2026, 11, 16, 7, 30),
                                dead_speak, activity_path=log)
            retry = self._fire(store, datetime.datetime(2026, 11, 16, 7, 45),
                               lambda text: {"ok": True}, activity_path=log)
            self.assertEqual(failed["outcome"], "failed")
            self.assertFalse(retry["ok"])

    # -- validity window (B-101) ------------------------------------
    def test_outside_window_recorded_missed_never_spoken(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "activity.log"
            log.write_text(_marker(16) + "\n", encoding="utf-8")
            store = self._store(tmp)
            dc.accept_offer(store, "07:00")
            result = self._fire(store, datetime.datetime(2026, 11, 16, 10, 30),
                                lambda text: calls.append(text) or {"ok": True},
                                activity_path=log)
            self.assertEqual(result["outcome"], "missed")
            self.assertEqual(calls, [])
            state = store.read()
            self.assertEqual(state["last_nudge_date"], "2026-11-16")
            self.assertEqual(state["last_nudge_outcome"], "missed")

    # -- quiet hours (B-103) -----------------------------------------
    def test_quiet_hours_stay_silent(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "activity.log"
            log.write_text(_marker(16) + "\n", encoding="utf-8")
            quiet = Path(tmp) / "daily_v562.json"
            quiet.write_text(json.dumps(
                {"quiet_hours": {"start": "06:00", "end": "09:00"}}),
                encoding="utf-8")
            store = self._store(tmp)
            dc.accept_offer(store, "07:00")
            result = self._fire(store, datetime.datetime(2026, 11, 16, 7, 30),
                                lambda text: calls.append(text) or {"ok": True},
                                activity_path=log, quiet_store_path=quiet)
            self.assertEqual(result["outcome"], "quiet")
            self.assertEqual(calls, [])

    # -- content (B-202) ---------------------------------------------
    def test_adversarial_titles_never_spoken(self):
        calls = []

        def adv_reminders():
            return ([{"label": "x", "fire_at": "2026-11-16T10:00:00+00:00",
                      "message": "Call mum about the SECRET deal"}], None)

        def adv_routines():
            return ([{"name": "My secret routine",
                      "next_run": "2026-11-16 18:00"}], None)

        def adv_events():
            return ([{"title": "Interview with Acme Corp",
                      "start": datetime.datetime(2026, 11, 16, 15, 0)}],
                    True, "")

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "activity.log"
            log.write_text(_marker(16) + "\n", encoding="utf-8")
            store = self._store(tmp)
            dc.accept_offer(store, "07:00")
            result = dc.fire_nudge(
                store, now=datetime.datetime(2026, 11, 16, 7, 30),
                speak_fn=lambda text: calls.append(text) or {"ok": True},
                activity_path=log, reminders_fn=adv_reminders,
                routines_fn=adv_routines, calendar_fn=adv_events)
            self.assertTrue(result["ok"])
            self.assertEqual(calls, ["3 things need you today."])
            self.assertEqual(result["counts"]["total"], 3)

    def test_count_read_at_fire_time_not_at_setup(self):
        calls = []
        state = {"reminders": []}

        def changing_reminders():
            return ([{"label": "r", "fire_at": "2026-11-16T10:00:00+00:00",
                      "message": "m"}] * len(state["reminders"]), None)

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "activity.log"
            log.write_text(_marker(16) + "\n", encoding="utf-8")
            store = self._store(tmp)
            dc.accept_offer(store, "07:00")
            # The count changes between the offer and the fire.
            state["reminders"] = [1, 2]
            result = dc.fire_nudge(
                store, now=datetime.datetime(2026, 11, 16, 7, 30),
                speak_fn=lambda text: calls.append(text) or {"ok": True},
                activity_path=log, reminders_fn=changing_reminders,
                routines_fn=_no_routines, calendar_fn=_no_events)
            self.assertTrue(result["ok"])
            self.assertEqual(calls, ["2 things need you today."])

    # -- the three pinned sources only -------------------------------
    def test_nudge_manifest_pins_exactly_three_sources(self):
        manifest = dc.nudge_manifest()
        self.assertEqual(manifest["payload"]["kind"], "daily_nudge")
        self.assertEqual(set(manifest["allowed_data"]),
                         {"activity_log", "calendar_upcoming",
                          "reminders", "routines"})
        ok, reason = validate_manifest(manifest,
                                       known_tools={"speak_text"})
        self.assertTrue(ok, reason)

    def test_job_engine_refuses_incomplete_nudge_manifest(self):
        manifest = dc.nudge_manifest()
        manifest["allowed_data"] = ["activity_log", "reminders"]
        job = {"job_id": "daily_nudge", "kind": "daily_nudge",
               "cron": {"hour": 7, "minute": 0},
               "manifest": manifest}
        result = run_job(job)
        self.assertFalse(result["ok"])
        self.assertIn("manifest", result.get("reason", ""))

    # -- opt-in / opt-out (B-106) --------------------------------------
    def test_offer_absent_before_marker_present_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "activity.log"
            log.write_text("", encoding="utf-8")
            store = self._store(tmp)
            self.assertFalse(dc.nudge_offer(store, log)["available"])
            log.write_text(_marker(16) + "\n", encoding="utf-8")
            offer = dc.nudge_offer(store, log)
            self.assertTrue(offer["available"])
            self.assertEqual(offer["default_time"], "07:00")
            self.assertTrue(offer["preview"])

    def test_optout_durable_across_restart_and_optin_still_reachable(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "activity.log"
            log.write_text(_marker(16) + "\n", encoding="utf-8")
            path = Path(tmp) / "daily_count.json"
            dc.decline_offer(dc.DailyCountStore(path))
            # A fresh instance on the same file: still opted out.
            self.assertFalse(
                dc.nudge_offer(dc.DailyCountStore(path), log)["available"])
            # The on-demand opt-in (Settings) still works.
            dc.accept_offer(dc.DailyCountStore(path), "08:30")
            self.assertTrue(dc.DailyCountStore(path).read()["opted_in"])


class StoreTests(unittest.TestCase):
    # -- the store's atomic write ------------------------------------
    def test_store_writes_valid_json_with_no_temp_files_left(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "daily_count.json"
            store = dc.DailyCountStore(path)
            dc.accept_offer(store, "07:00")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))
                             ["nudge_time"], "07:00")
            leftovers = [p for p in Path(tmp).iterdir()
                         if p.name.startswith(".daily_count")
                         or p.suffix == ".tmp"]
            self.assertEqual(leftovers, [])

    def test_corrupted_store_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "daily_count.json"
            path.write_text("{not json", encoding="utf-8")
            store = dc.DailyCountStore(path)
            state = store.read()
            self.assertFalse(state["opted_in"])
            self.assertEqual(state["nudge_time"], "07:00")
            dc.accept_offer(store, "08:00")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))
                             ["nudge_time"], "08:00")


class BriefingTests(unittest.TestCase):
    # -- the count in the morning briefing (B-104, B-203) ------------
    def test_digest_ends_with_daily_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "activity.log"
            log.write_text("\n".join([_marker(16), _marker(17),
                                      _marker(18), _marker(19)]) + "\n",
                           encoding="utf-8")
            result = run_digest(now=datetime.datetime(2026, 11, 19, 7, 0),
                                summariser=None, speak=False,
                                activity=log, calendar_fn=lambda: [])
            self.assertTrue(result["ok"])
            self.assertTrue(result["digest"]["text"].endswith(
                "Days used this week: 4/7 \u00b7 last week: 0/7"))
            self.assertEqual(result["digest"]["items"]["daily_count"]
                             ["this_week"], 4)


if __name__ == "__main__":
    unittest.main()
