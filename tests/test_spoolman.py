"""Suite for the filament pack (R19-7, Donkie/Spoolman inspiration).

Covers: the tool is registered (749 total, 22 properties, required
action, additionalProperties False); the licence marker accepts
EITHER state (PROPOSED or Parent-ruled — the parent flips it after
the worker's run); the census pins (749/382/367/162, params 2024,
grouped 747, closed 748, verified 301); the gate (free by design,
app-state — not in the confirm set, in the free set, every action
free, unknown actions refused by the handler); the vendor /
filament / spool flows (add/list/get/remove/use, honest refusals,
remaining weight and length derived from density and diameter);
the store (reads never create the file, the ledger verifies,
tampering breaks the chain); the bounds; no foreign imports
(stdlib + tree config only).
"""

import json
import math
import os
import shutil
import sqlite3
import tempfile
import unittest

import core.filament as fl
from core.funnel.tools_registry import tools as _tools

_HERE = os.path.dirname(os.path.abspath(__file__))
_TREE = os.path.dirname(_HERE)


class _SpoolmanBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="spoolman_test_")
        self._old_data_dir = fl.DATA_DIR
        fl.DATA_DIR = self.tmp
        self.store = fl._FilamentStore(
            path=os.path.join(self.tmp, "filament.sqlite3"))

    def tearDown(self):
        fl.DATA_DIR = self._old_data_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _f(self, **kw):
        kw.setdefault("_store", self.store)
        return fl._handler(**kw)

    def _vendor(self, name="Prusament", **kw):
        out = self._f(action="vendor", kind="add", name=name, **kw)
        self.assertEqual(out["status"], "ok", out)
        return out["vendor"]["id"]

    def _filament(self, vendor="Prusament", name="PLA Galaxy Black",
                  **kw):
        kw.setdefault("material", "PLA")
        kw.setdefault("density", 1.24)
        kw.setdefault("diameter", 1.75)
        kw.setdefault("weight", 1000)
        out = self._f(action="filament", kind="add", vendor=vendor,
                      name=name, **kw)
        self.assertEqual(out["status"], "ok", out)
        return out["filament"]["id"]

    def _spool(self, filament="PLA Galaxy Black", **kw):
        out = self._f(action="spool", kind="add", filament=filament,
                      **kw)
        self.assertEqual(out["status"], "ok", out)
        return out["spool"]["id"]


class TestRegistry(_SpoolmanBase):
    def test_filament_registered(self):
        decls = _tools.get_declarations()
        names = [d["name"] for d in decls]
        self.assertEqual(len(decls), 772)  # INTENTIONAL — R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(len(set(names)), 772)  # INTENTIONAL — R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertIn("filament", names)
        self.assertEqual(names.count("filament"), 1)

    def test_filament_declaration(self):
        decls = _tools.get_declarations()
        f = [d for d in decls if d["name"] == "filament"][0]
        self.assertEqual(len(f["parameters"]["properties"]), 22)
        self.assertEqual(f["parameters"]["required"], ["action"])
        self.assertIs(f["parameters"]["additionalProperties"], False)

    def test_params_total(self):
        decls = _tools.get_declarations()
        total = sum(len(d["parameters"]["properties"]) for d in decls)
        self.assertEqual(total, 2126)  # INTENTIONAL — R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_closed_total(self):
        decls = _tools.get_declarations()
        closed = sum(1 for d in decls
                     if d["parameters"].get("additionalProperties")
                     is False)
        self.assertEqual(closed, 771)  # INTENTIONAL — R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_grouped_total(self):
        from core.catalogue_snapshot import GROUP_MEMBERS
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 772)  # INTENTIONAL — R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170; UI Phase 2 (2026-11-25): grouped 769 -> 772 — click_anywhere + 2 plugin tools join the catalogue

    def test_filament_grouped_everyday_skills(self):
        from core.catalogue_snapshot import GROUP_MEMBERS
        self.assertIn("filament", GROUP_MEMBERS["Everyday skills"])
        # The single catalogue source (core/catalogue_groups_data.py,
        # GAPS-23 F-V7) is checked as text instead (the R19-4
        # vidbee precedent): find the "Everyday skills" tuple in
        # GROUP_MEMBERS and look inside it.
        text = open(os.path.join(_TREE, "core",
                                 "catalogue_groups_data.py"),
                    encoding="utf-8").read()
        start = text.index('"Everyday skills": (')
        end = text.index("\n    ),", start)
        self.assertIn('"filament",', text[start:end])

    def test_licence_marker_either_state(self):
        """The licence test accepts EITHER marker state (the parent
        flips it after the worker's run)."""
        text = open(os.path.join(_TREE, "core", "filament.py"),
                    encoding="utf-8").read()
        self.assertTrue(
            "PROPOSED — parent rules" in text
            or "Parent-ruled (licence, 2026-11-12)" in text,
            "the licence marker must be PROPOSED or Parent-ruled")
        self.assertIn("MIT", text)
        self.assertIn("Copyright (c) 2023 Daniel Hultgren", text)
        self.assertIn("1,072 bytes, 21 lines", text)


class TestGate(_SpoolmanBase):
    def test_filament_is_free_by_design(self):
        from core.funnel.tools_gate_free import _FREE_BY_DESIGN
        self.assertEqual(_FREE_BY_DESIGN.get("filament"), "app-state")

    def test_filament_not_in_confirm_set(self):
        from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
        self.assertNotIn("filament", _CONFIRM_GATE_TOOLS)
        self.assertEqual(len(_CONFIRM_GATE_TOOLS), 170)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_all_actions_are_free(self):
        from core.funnel.tools_gate import _needs_confirmation
        for action in ("vendor", "filament", "spool", "status",
                       "events"):
            self.assertFalse(
                _needs_confirmation("filament", {"action": action}),
                action)

    def test_unknown_action_refused_by_handler(self):
        out = self._f(action="bogus")
        self.assertEqual(out["status"], "error")
        self.assertIn("unknown action", out["message"])
        out = self._f()
        self.assertEqual(out["status"], "error")

    def test_readclass(self):
        from core.funnel.tools_gate_readclass import _is_read_action
        self.assertTrue(_is_read_action("filament",
                                        {"action": "status"}))
        self.assertTrue(_is_read_action("filament",
                                        {"action": "events"}))
        for action in ("vendor", "filament", "spool"):
            self.assertTrue(_is_read_action(
                "filament", {"action": action, "kind": "list"}))
            self.assertTrue(_is_read_action(
                "filament", {"action": action, "kind": "get"}))
            self.assertFalse(_is_read_action(
                "filament", {"action": action, "kind": "add"}))
            self.assertFalse(_is_read_action(
                "filament", {"action": action, "kind": "remove"}))
        self.assertFalse(_is_read_action(
            "filament", {"action": "spool", "kind": "use"}))


class TestVendor(_SpoolmanBase):
    def test_vendor_add_list_get_remove(self):
        vid = self._vendor("Prusament", comment="Prague",
                           spool_weight=250)
        out = self._f(action="vendor", kind="list")
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["vendors"][0]["name"], "Prusament")
        self.assertEqual(out["vendors"][0]["empty_spool_weight"], 250)
        out = self._f(action="vendor", kind="get", vendor=vid)
        self.assertEqual(out["vendor"]["comment"], "Prague")
        out = self._f(action="vendor", kind="get", vendor="Prusament")
        self.assertEqual(out["vendor"]["id"], vid)
        out = self._f(action="vendor", kind="remove", vendor=vid)
        self.assertEqual(out["status"], "ok")
        out = self._f(action="vendor", kind="list")
        self.assertEqual(out["count"], 0)

    def test_vendor_duplicate_and_unknown_refused(self):
        self._vendor("Prusament")
        out = self._f(action="vendor", kind="add", name="Prusament")
        self.assertEqual(out["status"], "error")
        self.assertIn("already exists", out["message"])
        out = self._f(action="vendor", kind="get", vendor="Nope")
        self.assertEqual(out["status"], "error")
        self.assertIn("no vendor", out["message"])
        out = self._f(action="vendor", kind="remove", vendor="Nope")
        self.assertEqual(out["status"], "error")

    def test_vendor_remove_names_filament_count(self):
        self._vendor("Prusament")
        self._filament()
        out = self._f(action="vendor", kind="remove",
                      vendor="Prusament")
        self.assertEqual(out["status"], "error")
        self.assertIn("1 filament type", out["message"])

    def test_vendor_bad_name_refused(self):
        out = self._f(action="vendor", kind="add", name="")
        self.assertEqual(out["status"], "error")
        out = self._f(action="vendor", kind="add", name="x" * 121)
        self.assertEqual(out["status"], "error")
        out = self._f(action="vendor", kind="bogus")
        self.assertEqual(out["status"], "error")


class TestFilament(_SpoolmanBase):
    def test_filament_add_list_get_remove(self):
        self._vendor("Prusament", spool_weight=250)
        fid = self._filament(color="#ff0000", article_number="A1")
        out = self._f(action="filament", kind="list")
        self.assertEqual(out["count"], 1)
        got = out["filaments"][0]
        self.assertEqual(got["vendor"], "Prusament")
        # Empty-spool weight falls back to the vendor's.
        self.assertEqual(got["spool_weight"], 250)
        self.assertEqual(got["color"], "#FF0000")
        out = self._f(action="filament", kind="get", filament=fid)
        self.assertEqual(out["filament"]["material"], "PLA")
        out = self._f(action="filament", kind="remove", filament=fid)
        self.assertEqual(out["status"], "ok")
        out = self._f(action="filament", kind="list")
        self.assertEqual(out["count"], 0)

    def test_filament_duplicate_per_vendor_refused(self):
        self._vendor("Prusament")
        self._filament()
        out = self._f(action="filament", kind="add", vendor="Prusament",
                      name="PLA Galaxy Black", material="PLA",
                      density=1.24, diameter=1.75)
        self.assertEqual(out["status"], "error")
        self.assertIn("already has", out["message"])
        # The same name under another vendor is fine.
        self._vendor("Bambu Lab")
        out = self._f(action="filament", kind="add", vendor="Bambu Lab",
                      name="PLA Galaxy Black", material="PLA",
                      density=1.24, diameter=1.75)
        self.assertEqual(out["status"], "ok", out)

    def test_filament_ambiguous_name_refused(self):
        self._vendor("Prusament")
        self._filament()
        self._vendor("Bambu Lab")
        self._f(action="filament", kind="add", vendor="Bambu Lab",
                name="PLA Galaxy Black", material="PLA", density=1.24,
                diameter=1.75)
        out = self._f(action="spool", kind="add",
                      filament="PLA Galaxy Black")
        self.assertEqual(out["status"], "error")
        self.assertIn("ambiguous", out["message"])

    def test_filament_bad_density_diameter_refused(self):
        self._vendor("Prusament")
        for kw in ({"density": 0}, {"density": 11},
                   {"diameter": 0}, {"diameter": 11},
                   {"density": "heavy"}, {"diameter": None}):
            out = self._f(action="filament", kind="add",
                          vendor="Prusament", name="X", material="PLA",
                          density=kw.get("density", 1.24),
                          diameter=kw.get("diameter", 1.75))
            self.assertEqual(out["status"], "error", kw)
        out = self._f(action="filament", kind="add", vendor="Nobody",
                      name="X", material="PLA", density=1.24,
                      diameter=1.75)
        self.assertEqual(out["status"], "error")
        self.assertIn("no vendor", out["message"])

    def test_filament_remove_names_spool_count(self):
        self._vendor("Prusament")
        self._filament()
        self._spool()
        out = self._f(action="filament", kind="remove",
                      filament="PLA Galaxy Black")
        self.assertEqual(out["status"], "error")
        self.assertIn("1 spool", out["message"])

    def test_filament_bad_color_refused(self):
        self._vendor("Prusament")
        out = self._f(action="filament", kind="add", vendor="Prusament",
                      name="X", material="PLA", density=1.24,
                      diameter=1.75, color="red")
        self.assertEqual(out["status"], "error")
        self.assertIn("hex colour", out["message"])


class TestSpool(_SpoolmanBase):
    def test_spool_add_remaining_derived(self):
        self._vendor("Prusament")
        self._filament()
        sid = self._spool(location="Shelf A", lot_number="L1",
                          price=2999)
        out = self._f(action="spool", kind="get", spool=sid)
        spool = out["spool"]
        self.assertEqual(spool["initial_weight"], 1000)
        self.assertEqual(spool["used_weight"], 0)
        self.assertEqual(spool["remaining_weight"], 1000)
        # PLA 1.24 g/cm3 at 1.75 mm is ~2.98 g/m: 1,000 g is ~335 m.
        self.assertAlmostEqual(spool["remaining_length_m"], 335.28,
                               places=1)
        self.assertEqual(spool["location"], "Shelf A")
        self.assertEqual(spool["lot_number"], "L1")
        self.assertEqual(spool["price"], 2999)
        self.assertFalse(spool["low_stock"])

    def test_spool_initial_defaults_to_filament_weight(self):
        self._vendor("Prusament")
        self._filament(weight=750)
        sid = self._spool()
        out = self._f(action="spool", kind="get", spool=sid)
        self.assertEqual(out["spool"]["initial_weight"], 750)

    def test_spool_initial_required_without_filament_weight(self):
        self._vendor("Prusament")
        self._filament(weight=None)
        out = self._f(action="spool", kind="add",
                      filament="PLA Galaxy Black")
        self.assertEqual(out["status"], "error")
        self.assertIn("initial_weight is required", out["message"])

    def test_spool_remaining_weight_input(self):
        self._vendor("Prusament")
        self._filament()
        sid = self._spool(initial_weight=1000, remaining_weight=400)
        out = self._f(action="spool", kind="get", spool=sid)
        self.assertEqual(out["spool"]["used_weight"], 600)
        self.assertEqual(out["spool"]["remaining_weight"], 400)

    def test_spool_used_and_remaining_both_refused(self):
        self._vendor("Prusament")
        self._filament()
        out = self._f(action="spool", kind="add",
                      filament="PLA Galaxy Black", initial_weight=1000,
                      used_weight=100, remaining_weight=400)
        self.assertEqual(out["status"], "error")
        self.assertIn("not both", out["message"])

    def test_spool_used_more_than_initial_refused(self):
        self._vendor("Prusament")
        self._filament()
        out = self._f(action="spool", kind="add",
                      filament="PLA Galaxy Black", initial_weight=100,
                      used_weight=101)
        self.assertEqual(out["status"], "error")
        out = self._f(action="spool", kind="add",
                      filament="PLA Galaxy Black", initial_weight=100,
                      remaining_weight=101)
        self.assertEqual(out["status"], "error")

    def test_spool_use_records_and_derives(self):
        self._vendor("Prusament")
        self._filament()
        sid = self._spool()
        out = self._f(action="spool", kind="use", spool=sid,
                      used_weight=250)
        self.assertEqual(out["status"], "ok")
        self.assertIn("250", out["message"])
        self.assertIn("750", out["message"])
        self.assertEqual(out["spool"]["used_weight"], 250)
        self.assertEqual(out["spool"]["remaining_weight"], 750)
        self.assertAlmostEqual(out["spool"]["remaining_length_m"],
                               251.46, places=1)
        out = self._f(action="spool", kind="use", spool=sid,
                      used_weight=100)
        self.assertEqual(out["spool"]["remaining_weight"], 650)

    def test_spool_use_shortfall_refused_with_numbers(self):
        self._vendor("Prusament")
        self._filament()
        sid = self._spool()
        self._f(action="spool", kind="use", spool=sid, used_weight=900)
        out = self._f(action="spool", kind="use", spool=sid,
                      used_weight=101)
        self.assertEqual(out["status"], "error")
        self.assertIn("only 100", out["message"])
        self.assertIn("101", out["message"])
        # The failed use changed nothing.
        out = self._f(action="spool", kind="get", spool=sid)
        self.assertEqual(out["spool"]["remaining_weight"], 100)

    def test_spool_use_zero_or_negative_refused(self):
        self._vendor("Prusament")
        self._filament()
        sid = self._spool()
        for grams in (0, -5):
            out = self._f(action="spool", kind="use", spool=sid,
                          used_weight=grams)
            self.assertEqual(out["status"], "error", grams)

    def test_spool_remove(self):
        self._vendor("Prusament")
        self._filament()
        sid = self._spool()
        out = self._f(action="spool", kind="remove", spool=sid)
        self.assertEqual(out["status"], "ok")
        out = self._f(action="spool", kind="list")
        self.assertEqual(out["count"], 0)
        out = self._f(action="spool", kind="get", spool=sid)
        self.assertEqual(out["status"], "error")

    def test_spool_unknown_kind_refused(self):
        out = self._f(action="spool", kind="polish")
        self.assertEqual(out["status"], "error")


class TestStatusEvents(_SpoolmanBase):
    def test_status_counts_and_low_stock(self):
        self._vendor("Prusament")
        self._filament()
        s1 = self._spool()
        s2 = self._spool()
        self._f(action="spool", kind="use", spool=s2, used_weight=950)
        out = self._f(action="status")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["vendors"], 1)
        self.assertEqual(out["filaments"], 1)
        self.assertEqual(out["spools"], 2)
        self.assertEqual(out["total_remaining_weight"], 1050)
        self.assertEqual(len(out["low_spools"]), 1)
        self.assertEqual(out["low_spools"][0]["id"], s2)
        self.assertTrue(out["ledger_ok"])
        self.assertEqual(out["ledger_events"], 5)
        _ = s1

    def test_status_empty_store(self):
        out = self._f(action="status")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["spools"], 0)
        self.assertEqual(out["ledger_events"], 0)

    def test_events_list_and_verify(self):
        self._vendor("Prusament")
        self._filament()
        self._spool()
        out = self._f(action="events", kind="verify")
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["ok"])
        out = self._f(action="events", kind="list")
        self.assertEqual(out["count"], 3)
        kinds = [e["kind"] for e in out["events"]]
        self.assertEqual(kinds, ["spool_add", "filament_add",
                                 "vendor_add"])
        out = self._f(action="events", kind="bogus")
        self.assertEqual(out["status"], "error")

    def test_reads_never_create_the_store(self):
        path = os.path.join(self.tmp, "filament.sqlite3")
        self.assertFalse(os.path.exists(path))
        for kw in ({"action": "status"},
                   {"action": "vendor", "kind": "list"},
                   {"action": "filament", "kind": "list"},
                   {"action": "spool", "kind": "list"},
                   {"action": "events", "kind": "list"}):
            out = self._f(**kw)
            self.assertEqual(out["status"], "ok", kw)
        self.assertFalse(os.path.exists(path))

    def test_tampered_ledger_fails_verification(self):
        self._vendor("Prusament")
        path = os.path.join(self.tmp, "filament.sqlite3")
        conn = sqlite3.connect(path)
        conn.execute("UPDATE events SET payload = '{}' WHERE id = 1")
        conn.commit()
        conn.close()
        out = self._f(action="events", kind="verify")
        # An error return carries status + message only.
        self.assertEqual(out["status"], "error")
        self.assertIn("BROKEN", out["message"])

    def test_limit_bounds(self):
        self._vendor("Prusament")
        out = self._f(action="vendor", kind="list", limit=0)
        self.assertEqual(out["status"], "error")
        out = self._f(action="vendor", kind="list", limit=101)
        self.assertEqual(out["status"], "error")
        out = self._f(action="vendor", kind="list", limit=1)
        self.assertEqual(out["count"], 1)

    def test_no_foreign_imports(self):
        """Only stdlib + the tree's own config may be imported."""
        import ast
        text = open(os.path.join(_TREE, "core", "filament.py"),
                    encoding="utf-8").read()
        tree = ast.parse(text)
        allowed = {"hashlib", "json", "math", "re", "sqlite3",
                   "threading", "time", "pathlib"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], allowed,
                                  alias.name)
            if isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    # A relative import: only the tree's own config
                    # — and the shared store base (GAPS-14), the
                    # tree's own stdlib-only module, same as config.
                    if node.module is None:
                        self.assertEqual(
                            [a.name for a in node.names], ["store_base"])
                    else:
                        self.assertEqual(node.module, "config")
                else:
                    self.assertIn(node.module.split(".")[0], allowed,
                                  node.module)


if __name__ == "__main__":
    unittest.main()
