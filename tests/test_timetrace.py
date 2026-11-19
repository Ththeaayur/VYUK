"""R18-10 dominikbraun/timetrace rebuild — local working-time tracker
(projects, start/stop, records, status, report, hash-chained ledger).

Every behavioural test drives a temp SQLite store (the module's
_get_store seam); nothing here touches the production store, the
network, or a live clock beyond the module's own time.time() calls.
Hours only — no rate, no amount, no invoicing (ruling 34) — and
nothing here builds a billing surface.
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".pkgroot"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import time_tracker as tt
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
    _needs_confirmation,
)
from core.funnel.tools_registry import tools as _registry

TREE = Path(__file__).resolve().parent.parent


class TimeTrackerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "tt.db")
        self._store_patch = mock.patch.object(
            tt, "_get_store", lambda: tt._TimeStore(Path(self.db)))
        self._store_patch.start()
        self.addCleanup(self._store_patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.h = tt.time_tracker

    def _add_project(self, name="Alpha", tags="deep"):
        out = self.h(action="project", mode="add", name=name, tags=tags)
        self.assertIn("added", out, out)
        return out

    def _start_stop(self, project="Alpha"):
        self.assertIn("Tracking", self.h(action="start", project=project))
        time.sleep(0.02)
        out = self.h(action="stop")
        self.assertIn("Stopped", out, out)
        return out

    # -- wiring ------------------------------------------------------
    def test_registry_total_is_757(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan
        self.assertEqual(len(_registry.get_declarations()), 757)  # INTENTIONAL — R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan

    def test_registry_params_total_is_2079(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; GAPS-20 loopback-meeting pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan
        total = sum(len(d.get("parameters", {}).get("properties", {}))
                    for d in _registry.get_declarations())
        self.assertEqual(total, 2079)  # INTENTIONAL — R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan

    def test_catalogue_grouped_is_754(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 754)  # INTENTIONAL — R19-1 videolingo pack (2026-11-12): 740 -> 741 — audio_localise grouped in Play & create; R19-2 ortools pack (2026-11-12): 741 -> 742 — optimise grouped in Everyday skills; R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan

    def test_registry_closed_is_756(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan
        closed = sum(1 for d in _registry.get_declarations()
                     if d.get("parameters", {}).get("additionalProperties")
                     is False)
        self.assertEqual(closed, 756)  # INTENTIONAL — R19-1 videolingo pack (2026-11-12): 741 -> 742 — audio_localise added (additionalProperties False by the registry default, so the new tool is closed); R19-2 ortools pack (2026-11-12): 742 -> 743 — optimise added (additionalProperties False by the registry default, so the new tool is closed); R19-3 aureuserp pack (2026-11-12): 743 -> 744 — erp added (additionalProperties False by the registry default, so the new tool is closed); R19-4 vidbee pack (2026-11-12): 744 -> 745 — media_chat added (additionalProperties False by the registry default, so the new tool is closed); R19-5 invoify pack (2026-11-12): 745 -> 746 — invoice added (additionalProperties False by the registry default, so the new tool is closed); R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan

    def test_time_tracker_in_everyday_skills(self):
        self.assertIn("time_tracker", GROUP_MEMBERS["Everyday skills"])

    def test_time_tracker_has_13_properties(self):
        decl = [d for d in _registry.get_declarations()
                if d["name"] == "time_tracker"]
        self.assertEqual(len(decl), 1)
        self.assertEqual(len(decl[0]["parameters"]["properties"]), 13)
        self.assertEqual(decl[0]["parameters"]["required"], ["action"])

    def test_time_tracker_is_free_by_design(self):
        self.assertEqual(_FREE_BY_DESIGN.get("time_tracker"), "app-state")
        self.assertNotIn("time_tracker", _CONFIRM_GATE_TOOLS)
        self.assertFalse(_needs_confirmation("time_tracker", {}))

    def test_classification_snapshot(self):
        snap = json.load(open(TREE / "tests" / "_classification_snapshot.json"))
        entry = snap["time_tracker"]
        self.assertEqual(entry["free"], "app-state")
        self.assertIs(entry["confirm"], False)
        self.assertIs(entry["read"], False)

    def test_licence_marker_is_proposed_or_parent_ruled(self):
        text = open(TREE / "core" / "time_tracker.py",
                    encoding="utf-8").read()
        self.assertTrue("PROPOSED — parent rules" in text
                        or "Parent-ruled" in text)

    def test_no_network_imports(self):
        text = open(TREE / "core" / "time_tracker.py",
                    encoding="utf-8").read()
        for bad in ("import urllib", "from urllib", "import requests",
                    "import socket", "http.client", "import subprocess"):
            self.assertNotIn(bad, text)

    def test_no_invoicing_surface(self):
        text = open(TREE / "core" / "time_tracker.py",
                    encoding="utf-8").read()
        for bad in ('"rate"', '"amount"', '"invoice"',
                    "'rate'", "'amount'", "'invoice'"):
            self.assertNotIn(bad, text)

    def test_reads_do_not_create_the_store(self):
        self.assertTrue(self.h(action="status").startswith("Nothing"))
        self.assertEqual(self.h(action="report"), "No records in that range.")
        self.assertEqual(self.h(action="events"), "No ledger events yet.")
        self.assertTrue(self.h(action="project", mode="list")
                        .startswith("No projects"))
        self.assertFalse(os.path.exists(self.db))

    # -- projects ------------------------------------------------------
    def test_project_add_list_get(self):
        self._add_project()
        self.assertIn("Alpha", self.h(action="project", mode="list"))
        self.assertIn("0 records",
                      self.h(action="project", mode="get", name="Alpha"))

    def test_project_add_duplicate_refused(self):
        self._add_project()
        self.assertTrue(self.h(action="project", mode="add", name="Alpha")
                        .startswith("project refused"))

    def test_project_edit_tags(self):
        self._add_project()
        out = self.h(action="project", mode="edit", name="Alpha",
                     tags="deep,focus")
        self.assertIn("updated", out, out)
        self.assertIn("focus",
                      self.h(action="project", mode="get", name="Alpha"))

    def test_project_edit_needs_a_change(self):
        self._add_project()
        self.assertTrue(self.h(action="project", mode="edit", name="Alpha")
                        .startswith("project refused"))

    # -- start / stop --------------------------------------------------
    def test_start_stop_round_trip(self):
        self._add_project()
        self.assertIn("Tracking", self.h(action="start", project="Alpha"))
        self.assertTrue(self.h(action="start", project="Alpha")
                        .startswith("start refused"))
        self.assertIn("Tracking", self.h(action="status"))
        self.assertIn("Stopped", self.h(action="stop"))
        self.assertTrue(self.h(action="stop").startswith("stop refused"))

    def test_start_unknown_project_refused(self):
        self.assertTrue(self.h(action="start", project="Nope")
                        .startswith("start refused"))

    # -- records -------------------------------------------------------
    def test_manual_record_add_list_get(self):
        self._add_project()
        now = time.time()
        out = self.h(action="record", mode="add", project="Alpha",
                     start=str(now - 7200), end=str(now - 3600),
                     tags="deep", name="morning block")
        self.assertIn("Record #", out, out)
        self.assertIn("morning block",
                      self.h(action="record", mode="list", project="Alpha"))
        self.assertIn("Record #1",
                      self.h(action="record", mode="get", record_id=1))

    def test_record_end_before_start_refused(self):
        self._add_project()
        now = time.time()
        self.assertTrue(self.h(action="record", mode="add", project="Alpha",
                               start=str(now - 3600), end=str(now - 7200))
                        .startswith("record refused"))

    def test_record_overlap_refused(self):
        self._add_project()
        now = time.time()
        self.h(action="record", mode="add", project="Alpha",
               start=str(now - 7200), end=str(now - 3600))
        out = self.h(action="record", mode="add", project="Alpha",
                     start=str(now - 5400), end=str(now - 1800))
        self.assertTrue(out.startswith("record refused"), out)
        self.assertIn("overlaps", out)

    def test_record_edit_and_remove(self):
        self._add_project()
        now = time.time()
        self.h(action="record", mode="add", project="Alpha",
               start=str(now - 7200), end=str(now - 3600))
        out = self.h(action="record", mode="edit", record_id=1,
                     tags="deep,review")
        self.assertIn("updated", out, out)
        out = self.h(action="record", mode="remove", record_id=1)
        self.assertIn("removed", out, out)
        self.assertTrue(self.h(action="record", mode="get", record_id=1)
                        .startswith("record refused"))

    # -- report / status -------------------------------------------------
    def test_report_decimal_hours(self):
        self._add_project()
        now = time.time()
        self.h(action="record", mode="add", project="Alpha",
               start=str(now - 7200), end=str(now - 3600))
        out = self.h(action="report", project="Alpha")
        self.assertIn("1.00 h (1h 00m)", out, out)
        self.assertIn("By tag:", out)

    def test_report_by_date(self):
        self._add_project()
        now = time.time()
        self.h(action="record", mode="add", project="Alpha",
               start=str(now - 7200), end=str(now - 3600))
        # The record's own date, not "today": a record made in the
        # first two hours after midnight starts yesterday, and a
        # report for today would (correctly) find nothing.
        day = time.strftime("%Y-%m-%d", time.localtime(now - 7200))
        self.assertIn("in total", self.h(action="report", date=day))

    def test_status_today_totals(self):
        self._add_project()
        self._start_stop()
        out = self.h(action="status")
        self.assertIn("Today", out)
        self.assertIn("Alpha", out)

    # -- remove / restore --------------------------------------------------
    def test_project_remove_restores_with_records(self):
        self._add_project()
        now = time.time()
        self.h(action="record", mode="add", project="Alpha",
               start=str(now - 7200), end=str(now - 3600))
        out = self.h(action="project", mode="remove", name="Alpha")
        self.assertIn("soft-delete", out, out)
        self.assertNotIn("Alpha", self.h(action="project", mode="list"))
        out = self.h(action="project", mode="add", name="Alpha")
        self.assertIn("restored with its records", out, out)
        self.assertIn("Alpha", self.h(action="project", mode="list"))

    # -- ledger ----------------------------------------------------------
    def test_events_and_tamper_detection(self):
        self._add_project()
        out = self.h(action="events")
        self.assertIn("Ledger events", out)
        self.assertIn("project_added", out)
        self.assertTrue(tt._TimeStore(self.db).verify_chain())
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE events SET hash = '00' WHERE id = 1")
        conn.commit()
        conn.close()
        self.assertFalse(tt._TimeStore(self.db).verify_chain())
        self.assertTrue(self.h(action="events").startswith("events refused"))

    def test_unknown_action_refused(self):
        self.assertTrue(self.h(action="nope")
                        .startswith("time_tracker refused"))


if __name__ == "__main__":
    unittest.main()
