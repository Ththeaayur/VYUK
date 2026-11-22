"""R17-6 openstays rebuild — lodging / property management. Fake
property and fake guests only: a scratch SQLite file per test, no
real property, no real guests, no network, no money moving.
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".pkgroot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import lodging as lg
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate_free import _FREE_BY_DESIGN
from core.funnel.tools_gate_readclass import _is_read_action
from core.funnel.tools_registry import tools

TREE = Path(__file__).resolve().parent.parent


def _iso(day):
    return day.isoformat()


class LodgingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "lodging.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    # -- helpers ------------------------------------------------------

    def _property(self):
        return lg.lodging(
            "property", property="Pine Cabins",
            timezone="America/Toronto", currency="CAD",
            tax_rate_bps=1300, turnover_nights=1,
            deposit="percent:50", cancel_policy="14:100;7:50;0:0",
            _db_path=self.db)

    def _unit(self, name="Cabin 1", cents=12000, min_nights=2):
        return lg.lodging(
            "unit", property="Pine Cabins", unit=name, type="cabin",
            sleeps=4, cents=cents, min_nights=min_nights,
            _db_path=self.db)

    def _setup(self):
        self.assertIn("Property Pine Cabins added", self._property())
        self.assertIn("Unit Cabin 1 added", self._unit())

    def _rate(self, start, end, cents=18000, min_nights=3):
        return lg.lodging(
            "rate", unit="Cabin 1", cents=cents, check_in=start,
            check_out=end, min_nights=min_nights, _db_path=self.db)

    def _book(self, guest="Maya Rao", start="2026-12-24",
              end="2026-12-27", contact="maya@example.test"):
        return lg.lodging(
            "book", unit="Cabin 1", guest=guest, contact=contact,
            check_in=start, check_out=end, _db_path=self.db)

    # -- property / unit / rate ---------------------------------------

    def test_property_add_show_and_list(self):
        self.assertIn("Property Pine Cabins added", self._property())
        shown = lg.lodging("property", property="Pine Cabins",
                           _db_path=self.db)
        self.assertIn("CAD", shown)
        self.assertIn("America/Toronto", shown)
        self.assertIn("13.00%", shown)
        self.assertIn("Pine Cabins (CAD, America/Toronto, 0 unit(s))",
                      lg.lodging("property", _db_path=self.db))

    def test_property_bad_timezone_deposit_cancel_refused(self):
        out = lg.lodging("property", property="Nowhere",
                         timezone="Mars/Olympus", _db_path=self.db)
        self.assertIn("not a known IANA timezone", out)
        out = lg.lodging("property", property="Nowhere",
                         deposit="half", _db_path=self.db)
        self.assertIn("deposit has to be", out)
        out = lg.lodging("property", property="Nowhere",
                         cancel_policy="soon:all", _db_path=self.db)
        self.assertIn("cancel_policy", out)
        out = lg.lodging("property", property="Nowhere",
                         tax_rate_bps=10001, _db_path=self.db)
        self.assertIn("0-10000", out)

    def test_unit_add_show_list_and_missing_property(self):
        self.assertIn("Property Pine Cabins added", self._property())
        self.assertIn("Unit Cabin 1 added", self._unit())
        shown = lg.lodging("unit", property="Pine Cabins",
                           unit="Cabin 1", _db_path=self.db)
        self.assertIn("cabin", shown)
        self.assertIn("CAD 120.00 / night", shown)
        self.assertIn("Cabin 1 (Pine Cabins, cabin, sleeps 4",
                      lg.lodging("unit", property="Pine Cabins",
                                 _db_path=self.db))
        missing = lg.lodging("unit", property="No Such Place",
                             unit="Cabin 9", _db_path=self.db)
        self.assertIn("No property named", missing)

    def test_rate_seasonal_override_and_default(self):
        self._setup()
        self.assertIn("Rate set for Cabin 1", self._rate(
            "2026-12-20", "2027-01-05"))
        # Inside the season the rate row wins over the unit default.
        out = lg.lodging("availability", unit="Cabin 1",
                         check_in="2026-12-24", check_out="2026-12-27",
                         _db_path=self.db)
        self.assertIn("CAD 540.00 for 3 night(s)", out)
        # Outside the season the unit default applies.
        out = lg.lodging("availability", unit="Cabin 1",
                         check_in="2027-06-01", check_out="2027-06-03",
                         _db_path=self.db)
        self.assertIn("CAD 240.00 for 2 night(s)", out)

    def test_min_nights_enforced(self):
        self._setup()
        self._rate("2026-12-20", "2027-01-05")
        out = self._book(start="2026-12-26", end="2026-12-28")
        self.assertIn("needs at least 3 nights", out)

    # -- availability / booking / the guarantee ------------------------

    def test_availability_quote_math(self):
        self._setup()
        self._rate("2026-12-20", "2027-01-05")
        out = lg.lodging("availability", unit="Cabin 1",
                         check_in="2026-12-24", check_out="2026-12-27",
                         _db_path=self.db)
        # 3 x 18000 = 54000; tax 13% single rounding = 7020;
        # total 61020; deposit 50% = 30510; balance 30510.
        self.assertIn("free", out)
        self.assertIn("CAD 540.00 for 3 night(s)", out)
        self.assertIn("tax CAD 70.20", out)
        self.assertIn("CAD 610.20 total", out)
        self.assertIn("CAD 305.10 deposit due", out)
        self.assertIn("CAD 305.10 balance", out)

    def test_book_success_and_folio_frozen(self):
        self._setup()
        self._rate("2026-12-20", "2027-01-05")
        out = self._book()
        self.assertIn("Stay #1 booked", out)
        self.assertIn("CAD 610.20 total", out)
        self.assertIn("Turnover holds the unit until 2026-12-28", out)
        detail = lg.lodging("list", property="Pine Cabins", detail=1,
                            _db_path=self.db)
        self.assertIn("#1 Cabin 1", detail)
        self.assertIn("30510c", detail)  # frozen deposit, cents

    def test_double_booking_refused(self):
        self._setup()
        self.assertIn("Stay #1 booked", self._book())
        self.assertIn("already held for those nights",
                      self._book(guest="Second Guest",
                                 start="2026-12-25", end="2026-12-28"))
        self.assertIn("already held for those nights",
                      self._book(guest="Third Guest",
                                 start="2026-12-26", end="2026-12-29"))

    def test_turnover_night_is_held(self):
        self._setup()
        self.assertIn("Stay #1 booked", self._book())
        # Stay #1 ends 2026-12-27; turnover holds 2026-12-28.
        out = lg.lodging("availability", unit="Cabin 1",
                         check_in="2026-12-27", check_out="2026-12-30",
                         _db_path=self.db)
        self.assertIn("not free", out)
        self.assertIn("stay #1", out)
        # The morning after the turnover night is free again.
        out = lg.lodging("availability", unit="Cabin 1",
                         check_in="2026-12-28", check_out="2026-12-31",
                         _db_path=self.db)
        self.assertIn("free", out)

    def test_trigger_fires_on_direct_insert(self):
        self._setup()
        self.assertIn("Stay #1 booked", self._book())
        db = lg._LodgingDB(self.db)
        conn = db._connect()
        try:
            with self.assertRaises(sqlite3.IntegrityError) as ctx:
                conn.execute(
                    "INSERT INTO stays (unit_id, guest_id, check_in,"
                    " check_out, blocked_until, status, folio_json,"
                    " rules_json, created_at, updated_at)"
                    " VALUES (1, 1, '2026-12-25', '2026-12-28',"
                    " '2026-12-29', 'reserved', '{}', '{}', 'x', 'x')")
            self.assertIn("double-booking", str(ctx.exception))
        finally:
            db.close()

    # -- cancel / check-in / check-out / no-show -----------------------

    def test_cancel_refund_full_and_none(self):
        self._setup()
        far = date.today() + timedelta(days=200)
        out = self._book(start=_iso(far),
                         end=_iso(far + timedelta(days=2)))
        self.assertIn("Stay #1 booked", out)
        out = lg.lodging("cancel", stay_id=1, _db_path=self.db)
        self.assertIn("Stay #1 cancelled", out)
        self.assertIn("100% back", out)
        near = date.today() + timedelta(days=3)
        out = self._book(guest="Late Guest", start=_iso(near),
                         end=_iso(near + timedelta(days=2)))
        self.assertIn("Stay #2 booked", out)
        out = lg.lodging("cancel", stay_id=2, _db_path=self.db)
        self.assertIn("0% back", out)
        # A cancelled stay cannot be cancelled again.
        self.assertIn("only a reserved stay can be cancelled",
                      lg.lodging("cancel", stay_id=2, _db_path=self.db))

    def test_checkin_requires_ready(self):
        self._setup()
        self.assertIn("Stay #1 booked", self._book())
        self.assertIn("ready -> dirty",
                      lg.lodging("housekeeping", unit="Cabin 1",
                                 state="dirty", _db_path=self.db))
        refused = lg.lodging("checkin", stay_id=1, _db_path=self.db)
        self.assertIn("cannot check in", refused)
        self.assertIn("not ready", refused)
        lg.lodging("housekeeping", unit="Cabin 1", state="cleaning",
                   _db_path=self.db)
        lg.lodging("housekeeping", unit="Cabin 1", state="ready",
                   _db_path=self.db)
        self.assertIn("Stay #1 checked in",
                      lg.lodging("checkin", stay_id=1, _db_path=self.db))

    def test_checkout_marks_dirty(self):
        self._setup()
        self.assertIn("Stay #1 booked", self._book())
        lg.lodging("checkin", stay_id=1, _db_path=self.db)
        out = lg.lodging("checkout", stay_id=1, _db_path=self.db)
        self.assertIn("Stay #1 checked out", out)
        self.assertIn("now dirty", out)
        state = lg.lodging("housekeeping", unit="Cabin 1",
                           _db_path=self.db)
        self.assertIn("housekeeping: dirty", state)

    def test_housekeeping_illegal_transition(self):
        self._setup()
        self.assertIn("not one of",
                      lg.lodging("housekeeping", unit="Cabin 1",
                                 state="sparkling", _db_path=self.db))
        lg.lodging("housekeeping", unit="Cabin 1", state="dirty",
                   _db_path=self.db)
        out = lg.lodging("housekeeping", unit="Cabin 1", state="ready",
                         _db_path=self.db)
        self.assertIn("cannot go from dirty to ready", out)
        self.assertIn("dirty -> cleaning",
                      lg.lodging("housekeeping", unit="Cabin 1",
                                 state="cleaning", _db_path=self.db))
        self.assertIn("cleaning -> ready",
                      lg.lodging("housekeeping", unit="Cabin 1",
                                 state="ready", _db_path=self.db))

    def test_no_show_frees_unit(self):
        self._setup()
        self.assertIn("Stay #1 booked", self._book())
        out = lg.lodging("no-show", stay_id=1, _db_path=self.db)
        self.assertIn("marked a no-show", out)
        self.assertIn("is free again", out)
        free = lg.lodging("availability", unit="Cabin 1",
                          check_in="2026-12-24", check_out="2026-12-27",
                          _db_path=self.db)
        self.assertIn("free", free)
        listed = lg.lodging("list", property="Pine Cabins",
                            state="no_show", _db_path=self.db)
        self.assertIn("#1 Cabin 1", listed)

    # -- guests / day / events ------------------------------------------

    def test_guest_record(self):
        self._setup()
        self.assertIn("Stay #1 booked", self._book())
        self.assertIn("Stay #2 booked",
                      self._book(start="2027-06-01", end="2027-06-03"))
        out = lg.lodging("guest", guest="Maya Rao", _db_path=self.db)
        self.assertIn("Maya Rao, maya@example.test", out)
        self.assertIn("#1 Cabin 1", out)
        self.assertIn("#2 Cabin 1", out)
        self.assertIn("No guest named",
                      lg.lodging("guest", guest="Nobody Here",
                                 _db_path=self.db))

    def test_day_view(self):
        self._setup()
        self._unit(name="Cabin 2")
        self.assertIn("Stay #1 booked", self._book())
        lg.lodging("book", unit="Cabin 2", guest="Early Guest",
                   check_in="2026-12-23", check_out="2026-12-26",
                   _db_path=self.db)
        lg.lodging("checkin", stay_id=2, _db_path=self.db)
        out = lg.lodging("day", property="Pine Cabins",
                         date="2026-12-24", _db_path=self.db)
        self.assertIn("1 arrival(s)", out)
        self.assertIn("0 departure(s)", out)
        self.assertIn("1 in-house", out)
        self.assertIn("occupancy 2/2", out)
        self.assertIn("arrives: #1 Cabin 1", out)
        self.assertIn("in-house: #2 Cabin 2", out)

    def test_events_hash_chain(self):
        self._setup()
        self.assertIn("Stay #1 booked", self._book())
        db = lg._LodgingDB(self.db)
        try:
            ok, err = db.verify_chain()
            self.assertTrue(ok, err)
            out = lg.lodging("events", limit=5, _db_path=self.db)
            self.assertIn("stay_booked", out)
            self.assertIn("chain OK", out)
            conn = db._connect()
            conn.execute(
                "UPDATE events SET body_json = '{\"tampered\": 1}'"
                " WHERE id = (SELECT MAX(id) FROM events)")
            conn.commit()
            ok, err = db.verify_chain()
            self.assertFalse(ok)
        finally:
            db.close()

    # -- validation -----------------------------------------------------

    def test_validation_refusals(self):
        self._setup()
        self.assertIn("not a real calendar date",
                      lg.lodging("availability", unit="Cabin 1",
                                 check_in="2026-13-40",
                                 check_out="2026-13-41",
                                 _db_path=self.db))
        self.assertIn("has to be after the start",
                      lg.lodging("availability", unit="Cabin 1",
                                 check_in="2026-12-27",
                                 check_out="2026-12-24",
                                 _db_path=self.db))
        self.assertIn("whole number of cents",
                      lg.lodging("unit", property="Pine Cabins",
                                 unit="Cabin 9", cents=120.5,
                                 _db_path=self.db))
        self.assertIn("whole number of cents",
                      lg.lodging("unit", property="Pine Cabins",
                                 unit="Cabin 9", cents=True,
                                 _db_path=self.db))
        self.assertIn("needs an action",
                      lg.lodging("teleport", _db_path=self.db))
        self.assertIn("A stay id is needed",
                      lg.lodging("cancel", _db_path=self.db))
        self.assertIn("No stay #99",
                      lg.lodging("cancel", stay_id=99, _db_path=self.db))

    # -- wiring ---------------------------------------------------------

    def test_census(self):
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 764)  # INTENTIONAL — R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free-by-design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2102)  # INTENTIONAL — R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        snap = json.loads((TREE / "tests" /
                           "_classification_snapshot.json").read_text())
        self.assertEqual(len(snap), 764)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(snap["lodging"],
                         {"confirm": False, "free": "app-state",
                          "read": False})

    def test_gate_free_and_readclass(self):
        self.assertEqual(_FREE_BY_DESIGN["lodging"], "app-state")
        for action in ("availability", "list", "guest", "day", "events"):
            self.assertTrue(_is_read_action(
                "lodging", {"action": action}), action)
        for action in ("property", "unit", "rate", "book", "cancel",
                       "checkin", "checkout", "no-show", "housekeeping"):
            self.assertFalse(_is_read_action(
                "lodging", {"action": action}), action)

    def test_catalogue_membership(self):
        self.assertIn("lodging", GROUP_MEMBERS["Your world"])
        text = (TREE / "core" / "catalogue_groups_data.py").read_text()
        lines = text.splitlines()
        bookings_at = next(i for i, line in enumerate(lines)
                           if line.strip() == '"bookings",')
        lodging_at = next(i for i, line in enumerate(lines)
                          if line.strip() == '"lodging",')
        self.assertEqual(lodging_at, bookings_at + 1)

    def test_audit_totals(self):
        text = (TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text()
        totals = json.loads(re.search(
            r"<!-- AUDIT-TOTALS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(totals["total"], 764)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(totals["read"], 384)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 382 -> 383 — one new read tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169
        self.assertEqual(totals["effectful"], 380)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374 -> 375 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(totals["confirm_gated"], 169)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 154 -> 155; R18-4 a2a pack (2026-11-12): 155 -> 156; R18-5 dspy pack (2026-11-12): 156 -> 157; R18-6 fonoster pack (2026-11-12): 157 -> 158; R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169
        self.assertEqual(totals["already_verified_in_handler"], 314)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 287 -> 288; R18-4 a2a pack (2026-11-12): 288 -> 289; R18-5 dspy pack (2026-11-12): 289 -> 290; R18-6 fonoster pack (2026-11-12): 290 -> 291; R18-7 attendize pack (2026-11-12): 291 -> 292; R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        counts = json.loads(re.search(
            r"<!-- AUDIT-COUNTS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(counts["productivity"], 148)  # INTENTIONAL — R18-7 attendize pack (2026-11-12): productivity 133 -> 134 (effectful 75 -> 76); R18-8 ta pack (2026-11-12): productivity 134 -> 135 (effectful 76 -> 77); R18-10 timetrace pack (2026-11-12): productivity 135 -> 136 (effectful 77 -> 78); R19-2 ortools pack (2026-11-12): productivity 136 -> 137 (effectful 78 -> 79); R19-3 aureuserp pack (2026-11-12): productivity 137 -> 138 (effectful 79 -> 80); R19-5 invoify pack (2026-11-12): productivity 138 -> 139 (effectful 80 -> 81); R19-6 tolgee pack (2026-11-12): productivity 139 -> 140 (effectful 81 -> 82); R19-7 spoolman pack (2026-11-12): productivity 140 -> 141 (effectful 82 -> 83); R19-8 djangocrm pack (2026-11-12): productivity 141 -> 142 (effectful 83 -> 84); R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 -> 145 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 -> 146 — one new tool run_plan; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        row = next(line for line in text.splitlines()
                   if line.startswith("| productivity |"))
        self.assertIn("| 148 | 90 |", row)  # INTENTIONAL — R18-7 attendize pack (2026-11-12): productivity 133 -> 134 (effectful 75 -> 76); R18-8 ta pack (2026-11-12): productivity 134 -> 135 (effectful 76 -> 77); R18-10 timetrace pack (2026-11-12): productivity 135 -> 136 (effectful 77 -> 78); R19-2 ortools pack (2026-11-12): productivity 136 -> 137 (effectful 78 -> 79); R19-3 aureuserp pack (2026-11-12): productivity 137 -> 138 (effectful 79 -> 80); R19-5 invoify pack (2026-11-12): productivity 138 -> 139 (effectful 80 -> 81); R19-6 tolgee pack (2026-11-12): productivity 139 -> 140 (effectful 81 -> 82); R19-7 spoolman pack (2026-11-12): productivity 140 -> 141 (effectful 82 -> 83); R19-8 djangocrm pack (2026-11-12): productivity 141 -> 142 (effectful 83 -> 84); R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 | 86 -> 145 | 87 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 | 87 -> 146 | 88 — one new tool run_plan; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169


if __name__ == "__main__":
    unittest.main()
