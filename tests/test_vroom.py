"""Suite for the vehicle_routes pack (R19-10, VROOM-Project/vroom
inspiration).

Covers: the tool is registered (751 total, 8 properties, required
action, additionalProperties False); the licence marker accepts
EITHER state (PROPOSED or Parent-ruled — the parent flips it after
the worker's run); the census pins (751/382/369/162, params 2053,
grouped 749, closed 750, verified 303); the gate (free by design,
app-state — not in the confirm set, in the free set, every action
free, unknown actions refused by the handler); the solver (time
windows, breaks, working hours, waiting, pickup-before-delivery,
skills, capacity); the plan flow (VRPTW, shipments, multi-depot,
unassigned with reasons, bounds, refusals); the store (reads never
create the file, the ledger verifies, tampering breaks the chain);
no foreign imports (stdlib + tree config only).
"""

import ast
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest

import core.vehicle_routes as rp
from core.funnel.tools_registry import tools as _tools

_HERE = os.path.dirname(os.path.abspath(__file__))
_TREE = os.path.dirname(_HERE)


class _VroomBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vroom_test_")
        self.store = rp._RPStore(
            path=os.path.join(self.tmp, "vehicle_routes.sqlite3"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _f(self, **kw):
        kw.setdefault("_store", self.store)
        return rp.vehicle_routes(**kw)

    def _pts(self):
        return [[0, 0], [1, 0], [0, 1], [2, 2]]

    def _vehicles(self):
        return [{"id": "V1", "capacity": 10, "start": 0, "end": 0,
                 "start_time": 0, "end_time": 100}]

    def _jobs(self):
        return [
            {"id": "J1", "location": 1, "demand": 2, "service": 1,
             "window": [0, 10]},
            {"id": "J2", "location": 2, "demand": 3, "service": 1,
             "window": [5, 15]},
            {"id": "J3", "location": 3, "demand": 2, "service": 1,
             "window": [0, 20]},
        ]

    def _plan(self, **kw):
        kw.setdefault("points", self._pts())
        kw.setdefault("vehicles", self._vehicles())
        kw.setdefault("jobs", self._jobs())
        out = self._f(action="plan", **kw)
        self.assertEqual(out["status"], "ok", out)
        return out


class TestRegistry(_VroomBase):
    def test_vehicle_routes_registered(self):
        decls = _tools.get_declarations()
        names = [d["name"] for d in decls]
        self.assertEqual(len(decls), 772)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(len(set(names)), 772)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertIn("vehicle_routes", names)
        self.assertEqual(names.count("vehicle_routes"), 1)

    def test_vehicle_routes_declaration(self):
        decls = _tools.get_declarations()
        d = [x for x in decls if x["name"] == "vehicle_routes"][0]
        self.assertEqual(len(d["parameters"]["properties"]), 8)
        self.assertEqual(d["parameters"]["required"], ["action"])
        self.assertIs(d["parameters"]["additionalProperties"], False)
        self.assertEqual(
            d["parameters"]["properties"]["action"]["enum"],
            ["plan", "status", "events"])

    def test_params_total(self):
        decls = _tools.get_declarations()
        total = sum(len(d["parameters"]["properties"]) for d in decls)
        self.assertEqual(total, 2126)  # INTENTIONAL — R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_closed_total(self):
        decls = _tools.get_declarations()
        closed = sum(1 for d in decls
                     if d["parameters"].get("additionalProperties")
                     is False)
        self.assertEqual(closed, 771)  # INTENTIONAL — R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_grouped_total(self):
        from core.catalogue_snapshot import GROUP_MEMBERS
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 769)  # INTENTIONAL — R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_vehicle_routes_grouped_everyday_skills(self):
        from core.catalogue_snapshot import GROUP_MEMBERS
        self.assertIn("vehicle_routes", GROUP_MEMBERS["Everyday skills"])
        # The single catalogue source (core/catalogue_groups_data.py,
        # GAPS-23 F-V7) is checked as text instead (the R19-4
        # vidbee precedent): find the "Everyday skills" tuple in
        # GROUP_MEMBERS and look inside it.
        text = open(os.path.join(_TREE, "core",
                                 "catalogue_groups_data.py"),
                    encoding="utf-8").read()
        start = text.index('"Everyday skills": (')
        end = text.index("\n    ),", start)
        self.assertIn('"vehicle_routes",', text[start:end])

    def test_licence_marker_either_state(self):
        """The licence test accepts EITHER marker state (the parent
        flips it after the worker's run)."""
        text = open(os.path.join(_TREE, "core", "vehicle_routes.py"),
                    encoding="utf-8").read()
        self.assertTrue(
            "PROPOSED — parent rules" in text
            or "Parent-ruled (licence, 2026-11-12)" in text,
            "the licence marker must be PROPOSED or Parent-ruled")
        self.assertIn("BSD-2-Clause", text)
        self.assertIn("Copyright (c) 2015-2025, Julien Coupey", text)
        self.assertIn("1,296 bytes, 24 lines", text)

    def test_audit_totals(self):
        text = open(os.path.join(_TREE, "TOOLS_LOOPHOLES_AUDIT.md"),
                    encoding="utf-8").read()
        for needle in ('"total": 772', '"read": 386',  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
                       '"effectful": 386', '"confirm_gated": 170',  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374/164 -> 375/165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
                       '"already_verified_in_handler": 320',  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
                       '"productivity": 152'):  # INTENTIONAL — GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 -> 145 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 -> 146 — one new tool run_plan; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
            self.assertIn(needle, text, needle)
        totals = json.loads(re.search(
            r"<!-- AUDIT-TOTALS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(totals["total"], 772)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(totals["read"], 386)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 382 -> 383 — one new read tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(totals["effectful"], 386)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374 -> 375 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(totals["confirm_gated"], 170)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(totals["already_verified_in_handler"], 320)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        counts = json.loads(re.search(
            r"<!-- AUDIT-COUNTS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(counts["productivity"], 152)  # INTENTIONAL — R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 -> 145 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 -> 146 — one new tool run_plan; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        row = next(line for line in text.splitlines()
                   if line.startswith("| productivity |"))
        self.assertIn("| 152 | 94 |", row)  # INTENTIONAL — R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 | 86 -> 145 | 87 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 | 87 -> 146 | 88 — one new tool run_plan; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertEqual(text.count("Counts after R19-10"), 1)
        self.assertIn("| `vehicle_routes` |", text)


class TestGate(_VroomBase):
    def test_vehicle_routes_is_free_by_design(self):
        from core.funnel.tools_gate_free import _FREE_BY_DESIGN
        self.assertEqual(_FREE_BY_DESIGN.get("vehicle_routes"), "app-state")

    def test_vehicle_routes_not_in_confirm_set(self):
        from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
        self.assertNotIn("vehicle_routes", _CONFIRM_GATE_TOOLS)
        self.assertEqual(len(_CONFIRM_GATE_TOOLS), 170)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_all_actions_are_free(self):
        from core.funnel.tools_gate import _needs_confirmation
        for action in ("plan", "status", "events"):
            self.assertFalse(
                _needs_confirmation("vehicle_routes", {"action": action}),
                action)

    def test_unknown_action_refused_by_handler(self):
        out = self._f(action="bogus")
        self.assertEqual(out["status"], "error")
        self.assertIn("unknown action", out["message"])
        out = self._f()
        self.assertEqual(out["status"], "error")

    def test_readclass(self):
        from core.funnel.tools_gate_readclass import _is_read_action
        self.assertTrue(_is_read_action("vehicle_routes",
                                        {"action": "status"}))
        self.assertTrue(_is_read_action("vehicle_routes",
                                        {"action": "events"}))
        self.assertFalse(_is_read_action("vehicle_routes",
                                         {"action": "plan"}))


class TestSolver(_VroomBase):
    def _timeline(self, route, vehicle, jobs, shipments, matrix):
        pj, by_id = rp._parse_jobs(jobs, len(matrix))
        pv = rp._parse_vehicles([vehicle], len(matrix))
        return rp._route_timeline(route, pv[0], by_id, shipments,
                                  matrix)

    def test_break_delays_service(self):
        matrix = rp._parse_points([[0, 0], [5, 0]])
        vehicle = {"id": "V1", "capacity": 10, "start": 0, "end": 0,
                   "start_time": 0, "end_time": 100,
                   "breaks": [[6, 10]]}
        jobs = [{"id": "J1", "location": 1, "demand": 1, "service": 3,
                 "window": [0, 50]}]
        ok, _c, tl, _r = self._timeline(["J1"], vehicle, jobs, [],
                                        matrix)
        self.assertTrue(ok)
        self.assertEqual(tl[0]["service_start"], 10.0)

    def test_break_past_window_is_infeasible(self):
        matrix = rp._parse_points([[0, 0], [5, 0]])
        vehicle = {"id": "V1", "capacity": 10, "start": 0, "end": 0,
                   "start_time": 0, "end_time": 100,
                   "breaks": [[6, 10]]}
        jobs = [{"id": "J1", "location": 1, "demand": 1, "service": 3,
                 "window": [0, 8]}]
        ok, _c, _t, reason = self._timeline(["J1"], vehicle, jobs, [],
                                            matrix)
        self.assertFalse(ok)
        self.assertIn("window closes", reason)

    def test_working_hours_refuse_late_return(self):
        matrix = rp._parse_points([[0, 0], [5, 0]])
        vehicle = {"id": "V1", "capacity": 10, "start": 0, "end": 0,
                   "start_time": 0, "end_time": 5}
        jobs = [{"id": "J1", "location": 1, "demand": 1, "service": 3,
                 "window": [0, 50]}]
        ok, _c, _t, reason = self._timeline(["J1"], vehicle, jobs, [],
                                            matrix)
        self.assertFalse(ok)
        self.assertIn("working hours", reason)

    def test_arriving_after_window_is_infeasible(self):
        matrix = rp._parse_points([[0, 0], [5, 0]])
        vehicle = {"id": "V1", "capacity": 10, "start": 0, "end": 0,
                   "start_time": 0, "end_time": 100}
        jobs = [{"id": "J1", "location": 1, "demand": 1, "service": 1,
                 "window": [0, 3]}]
        ok, _c, _t, reason = self._timeline(["J1"], vehicle, jobs, [],
                                            matrix)
        self.assertFalse(ok)
        self.assertIn("window closes", reason)

    def test_arriving_early_waits(self):
        matrix = rp._parse_points([[0, 0], [5, 0]])
        vehicle = {"id": "V1", "capacity": 10, "start": 0, "end": 0,
                   "start_time": 0, "end_time": 100}
        jobs = [{"id": "J1", "location": 1, "demand": 1, "service": 1,
                 "window": [20, 50]}]
        ok, _c, tl, _r = self._timeline(["J1"], vehicle, jobs, [],
                                        matrix)
        self.assertTrue(ok)
        self.assertEqual(tl[0]["service_start"], 20.0)

    def test_pickup_before_delivery(self):
        matrix = rp._parse_points([[0, 0], [5, 0]])
        vehicle = {"id": "V1", "capacity": 10, "start": 0, "end": 0,
                   "start_time": 0, "end_time": 100}
        jobs = [{"id": "P", "location": 1, "demand": 1},
                {"id": "D", "location": 1, "demand": 1}]
        ok, _c, _t, reason = self._timeline(["D", "P"], vehicle, jobs,
                                            [("P", "D")], matrix)
        self.assertFalse(ok)
        self.assertIn("before pickup", reason)
        ok, _c, _t, _r = self._timeline(["P", "D"], vehicle, jobs,
                                        [("P", "D")], matrix)
        self.assertTrue(ok)

    def test_skill_mismatch_is_infeasible(self):
        matrix = rp._parse_points([[0, 0], [5, 0]])
        vehicle = {"id": "V1", "capacity": 10, "start": 0, "end": 0,
                   "start_time": 0, "end_time": 100}
        jobs = [{"id": "J1", "location": 1, "demand": 1,
                 "skills": ["crane"]}]
        ok, _c, _t, reason = self._timeline(["J1"], vehicle, jobs, [],
                                            matrix)
        self.assertFalse(ok)
        self.assertIn("lacks the skills", reason)

    def test_over_capacity_is_infeasible(self):
        matrix = rp._parse_points([[0, 0], [5, 0]])
        vehicle = {"id": "V1", "capacity": 10, "start": 0, "end": 0,
                   "start_time": 0, "end_time": 100}
        jobs = [{"id": "J1", "location": 1, "demand": 99}]
        ok, _c, _t, reason = self._timeline(["J1"], vehicle, jobs, [],
                                            matrix)
        self.assertFalse(ok)
        self.assertIn("exceeds", reason)


class TestPlan(_VroomBase):
    def test_vrptw_plan_serves_every_job(self):
        out = self._plan()
        self.assertEqual(out["served"], 3)
        self.assertEqual(out["unassigned"], [])
        self.assertEqual(len(out["routes"]), 1)
        route = out["routes"][0]
        self.assertEqual(route["vehicle"], "V1")
        self.assertEqual(sorted(route["jobs"]), ["J1", "J2", "J3"])
        self.assertEqual(route["load"], 7)
        # Every returned route re-verifies feasible through the
        # timeline function.
        pj, by_id = rp._parse_jobs(self._jobs(), 4)
        pv = rp._parse_vehicles(self._vehicles(), 4)
        mat = rp._parse_points(self._pts())
        ok, _c, _t, _r = rp._route_timeline(
            route["jobs"], pv[0], by_id, [], mat)
        self.assertTrue(ok)

    def test_plan_with_matrix_instead_of_points(self):
        matrix = [[0, 1, 1, 2.8284271247461903],
                  [1, 0, 1.4142135623730951, 2.23606797749979],
                  [1, 1.4142135623730951, 0, 2.23606797749979],
                  [2.8284271247461903, 2.23606797749979,
                   2.23606797749979, 0]]
        out = self._plan(distance_matrix=matrix, points=None)
        self.assertEqual(out["served"], 3)
        self.assertEqual(out["unassigned"], [])

    def test_shipment_multi_depot_and_skills(self):
        pts = [[0, 0], [10, 0], [1, 0], [2, 0], [9, 0]]
        vehicles = [
            {"id": "A", "capacity": 5, "skills": ["cold"],
             "start": 0, "end": 0, "start_time": 0, "end_time": 50},
            {"id": "B", "capacity": 5, "skills": [],
             "start": 4, "end": 4, "start_time": 0, "end_time": 50},
        ]
        jobs = [
            {"id": "P", "location": 2, "demand": 1, "service": 1,
             "skills": ["cold"]},
            {"id": "D", "location": 3, "demand": 1, "service": 1,
             "skills": ["cold"]},
            {"id": "X", "location": 1, "demand": 1, "service": 1},
        ]
        out = self._plan(points=pts, vehicles=vehicles, jobs=jobs,
                         shipments=[{"pickup": "P", "delivery": "D"}])
        self.assertEqual(out["served"], 3)
        self.assertEqual(out["unassigned"], [])
        ship_route = next(r for r in out["routes"]
                          if "P" in r["jobs"])
        self.assertIn("D", ship_route["jobs"])
        self.assertLess(ship_route["jobs"].index("P"),
                        ship_route["jobs"].index("D"))
        # The cold shipment rides the skilled vehicle.
        self.assertEqual(ship_route["vehicle"], "A")

    def test_unassigned_reasons(self):
        jobs = [
            {"id": "J1", "location": 1, "demand": 2, "service": 1,
             "window": [0, 10]},
            {"id": "J2", "location": 2, "demand": 3, "service": 1,
             "skills": ["crane"]},
            {"id": "J3", "location": 3, "demand": 99, "service": 1},
        ]
        out = self._plan(jobs=jobs)
        self.assertEqual(out["served"], 1)
        un = {u["jobs"][0]: u["reason"] for u in out["unassigned"]}
        self.assertIn("J2", un)
        self.assertIn("skills", un["J2"])
        self.assertIn("J3", un)
        self.assertIn("capacity", un["J3"])

    def test_priority_serves_first(self):
        # A low-priority job with a tight window and a high-priority
        # job with a loose window: the high-priority job is placed
        # first, so both still fit — but the high-priority job must
        # appear in the served set even when capacity is tight.
        jobs = [
            {"id": "LOW", "location": 1, "demand": 9, "service": 1,
             "priority": 0},
            {"id": "HIGH", "location": 2, "demand": 9, "service": 1,
             "priority": 10},
        ]
        out = self._plan(jobs=jobs)
        # Only one of the two fits the capacity of 10.
        self.assertEqual(out["served"], 1)
        served = [j for r in out["routes"] for j in r["jobs"]]
        self.assertIn("HIGH", served)

    def test_bad_matrix_refused(self):
        out = self._f(action="plan", distance_matrix=[[0, 1], [1]],
                      vehicles=self._vehicles(), jobs=self._jobs())
        self.assertEqual(out["status"], "error")
        out = self._f(action="plan",
                      distance_matrix=[[1, 1], [1, 0]],
                      vehicles=self._vehicles(), jobs=self._jobs())
        self.assertEqual(out["status"], "error")

    def test_no_points_or_matrix_refused(self):
        out = self._f(action="plan", vehicles=self._vehicles(),
                      jobs=self._jobs())
        self.assertEqual(out["status"], "error")
        self.assertIn("points or a distance_matrix", out["message"])

    def test_empty_vehicles_refused(self):
        out = self._f(action="plan", points=self._pts(), vehicles=[],
                      jobs=self._jobs())
        self.assertEqual(out["status"], "error")

    def test_bad_job_refused(self):
        jobs = [{"id": "J1", "location": 99, "demand": 1}]
        out = self._f(action="plan", points=self._pts(),
                      vehicles=self._vehicles(), jobs=jobs)
        self.assertEqual(out["status"], "error")
        jobs = [{"id": "J1", "location": 1, "demand": -1}]
        out = self._f(action="plan", points=self._pts(),
                      vehicles=self._vehicles(), jobs=jobs)
        self.assertEqual(out["status"], "error")
        jobs = [{"id": "J1", "location": 1, "demand": 1,
                 "window": [10, 5]}]
        out = self._f(action="plan", points=self._pts(),
                      vehicles=self._vehicles(), jobs=jobs)
        self.assertEqual(out["status"], "error")

    def test_shipment_to_itself_refused(self):
        out = self._f(action="plan", points=self._pts(),
                      vehicles=self._vehicles(), jobs=self._jobs(),
                      shipments=[{"pickup": "J1", "delivery": "J1"}])
        self.assertEqual(out["status"], "error")

    def test_shipment_unknown_job_refused(self):
        out = self._f(action="plan", points=self._pts(),
                      vehicles=self._vehicles(), jobs=self._jobs(),
                      shipments=[{"pickup": "J1", "delivery": "NOPE"}])
        self.assertEqual(out["status"], "error")

    def test_job_in_two_shipments_refused(self):
        out = self._f(action="plan", points=self._pts(),
                      vehicles=self._vehicles(), jobs=self._jobs(),
                      shipments=[{"pickup": "J1", "delivery": "J2"},
                                 {"pickup": "J1", "delivery": "J3"}])
        self.assertEqual(out["status"], "error")

    def test_too_many_vehicles_refused(self):
        vehicles = [{"id": "V%d" % i, "capacity": 10}
                    for i in range(51)]
        out = self._f(action="plan", points=self._pts(),
                      vehicles=vehicles, jobs=self._jobs())
        self.assertEqual(out["status"], "error")
        self.assertIn("at most 50 vehicles", out["message"])


class TestStatusEvents(_VroomBase):
    def test_status_counts(self):
        self._plan()
        self._plan()
        out = self._f(action="status")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["plans"], 2)
        self.assertTrue(out["chain_ok"])
        self.assertEqual(out["events"], 2)

    def test_status_empty_store(self):
        out = self._f(action="status")
        self.assertEqual(out["status"], "ok")
        self.assertIn("no route plans yet", out["message"])
        self.assertEqual(out["plans"], 0)
        self.assertTrue(out["chain_ok"])

    def test_events_list_and_verify(self):
        self._plan()
        out = self._f(action="events")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(out["events"]), 1)
        self.assertEqual(out["events"][0]["kind"], "plan")
        out = self._f(action="events", mode="verify")
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["chain_ok"])
        out = self._f(action="events", mode="bogus")
        self.assertEqual(out["status"], "error")

    def test_reads_never_create_the_store(self):
        self._f(action="status")
        self._f(action="events")
        self.assertFalse(os.path.exists(
            os.path.join(self.tmp, "vehicle_routes.sqlite3")))

    def test_tampered_ledger_fails_verification(self):
        self._plan()
        conn = sqlite3.connect(
            os.path.join(self.tmp, "vehicle_routes.sqlite3"))
        conn.execute("UPDATE events SET hash = ? WHERE id = 1",
                     ("0" * 64,))
        conn.commit()
        conn.close()
        self.assertFalse(self.store.verify_chain())
        out = self._f(action="events", mode="verify")
        self.assertEqual(out["status"], "error")

    def test_limit_bounds(self):
        self._plan()
        out = self._f(action="events", limit=0)
        self.assertEqual(out["status"], "error")
        out = self._f(action="events", limit=1)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(out["events"]), 1)

    def test_no_foreign_imports(self):
        text = open(os.path.join(_TREE, "core", "vehicle_routes.py"),
                    encoding="utf-8").read()
        tree = ast.parse(text)
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in (
                            "vroom", "ortools", "requests", "osrm",
                            "valhalla"):
                        bad.append(alias.name)
            if isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] in (
                        "vroom", "ortools", "requests", "osrm",
                        "valhalla"):
                    bad.append(node.module)
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
