"""R19-2 google/or-tools rebuild — a combinatorial optimisation
engine in own stdlib code (TSP/VRP heuristics, bin packing, knapsack
DP, Dijkstra, Hungarian assignment, Edmonds-Karp max flow, two-phase
simplex LP, a small backtracking constraint solver) over a
hash-chained ledger.

Every behavioural test drives a temp SQLite store (the handler's
_store seam); nothing here touches the production store, the
network, or a live clock beyond the module's own time.time() calls.
No OR-Tools library, no C++, no subprocess, no network. TSP and VRP
are heuristics (the answer says so); the constraint solver is a small
backtracking solver, NOT CP-SAT.
"""
import itertools
import json
import math
import os
import random
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".pkgroot"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import optimise as opt
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
    _needs_confirmation,
)
from core.funnel.tools_registry import tools as _registry

TREE = Path(__file__).resolve().parent.parent


class OptimiseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "opt.db")
        self.store = opt._OptStore(path=self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def h(self, **kw):
        return opt.optimise(_store=self.store, **kw)

    # -- registry / census / catalogue / classification -----------------

    def test_registry_total_is_772(self):  # INTENTIONAL — R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(len(_registry.get_declarations()), 772)  # INTENTIONAL — R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_registry_params_total_is_2126(self):  # INTENTIONAL — R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; GAPS-20 loopback-meeting pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        total = sum(len(d.get("parameters", {}).get("properties", {}))
                    for d in _registry.get_declarations())
        self.assertEqual(total, 2126)  # INTENTIONAL — R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_catalogue_grouped_is_769(self):  # INTENTIONAL — R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 769)  # INTENTIONAL — R19-2 ortools pack (2026-11-12): 741 -> 742 — optimise grouped in Everyday skills; R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_registry_closed_is_771(self):  # INTENTIONAL — R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        closed = sum(1 for d in _registry.get_declarations()
                     if d.get("parameters", {}).get("additionalProperties")
                     is False)
        self.assertEqual(closed, 771)  # INTENTIONAL — R19-2 ortools pack (2026-11-12): 742 -> 743 — optimise added (additionalProperties False by the registry default, so the new tool is closed); R19-3 aureuserp pack (2026-11-12): 743 -> 744 — erp added (additionalProperties False by the registry default, so the new tool is closed); R19-4 vidbee pack (2026-11-12): 744 -> 745 — media_chat added (additionalProperties False by the registry default, so the new tool is closed); R19-5 invoify pack (2026-11-12): 745 -> 746 — invoice added (additionalProperties False by the registry default, so the new tool is closed); R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_optimise_in_everyday_skills(self):
        self.assertIn("optimise", GROUP_MEMBERS["Everyday skills"])

    def test_optimise_has_19_properties(self):
        decl = [d for d in _registry.get_declarations()
                if d["name"] == "optimise"]
        self.assertEqual(len(decl), 1)
        self.assertEqual(len(decl[0]["parameters"]["properties"]), 19)
        self.assertEqual(decl[0]["parameters"]["required"], ["action"])

    def test_optimise_is_free_by_design(self):
        self.assertEqual(_FREE_BY_DESIGN.get("optimise"), "app-state")
        self.assertNotIn("optimise", _CONFIRM_GATE_TOOLS)
        self.assertFalse(_needs_confirmation("optimise", {}))

    def test_classification_snapshot(self):
        snap = json.load(open(TREE / "tests" / "_classification_snapshot.json"))
        entry = snap["optimise"]
        self.assertEqual(entry["free"], "app-state")
        self.assertIs(entry["confirm"], False)
        self.assertIs(entry["read"], False)

    def test_licence_marker_is_proposed_or_parent_ruled(self):
        text = open(TREE / "core" / "optimise.py",
                    encoding="utf-8").read()
        self.assertTrue("PROPOSED — parent rules" in text
                        or "Parent-ruled" in text)

    def test_no_network_imports(self):
        text = open(TREE / "core" / "optimise.py",
                    encoding="utf-8").read()
        for banned in ("import urllib", "from urllib", "import requests",
                       "import socket", "http.client", "import subprocess",
                       "os.system", "Popen", "import ortools",
                       "from ortools", "streamlit"):
            self.assertNotIn(banned, text)

    def test_no_min_cost_flow_or_mip_action(self):
        self.assertNotIn("min_cost_flow", opt._ACTIONS)
        self.assertNotIn("mip", opt._ACTIONS)
        self.assertNotIn("cp_sat", opt._ACTIONS)

    # -- store guard -----------------------------------------------------

    def test_reads_do_not_create_the_store_file(self):
        self.assertEqual(self.h(action="status"),
                         "No solves yet — the ledger is empty.")
        self.assertFalse(os.path.exists(self.db))
        self.assertEqual(self.h(action="events"),
                         "No solves yet — the ledger is empty.")
        self.assertFalse(os.path.exists(self.db))

    # -- TSP --------------------------------------------------------------

    def test_tsp_unit_square_is_optimal(self):
        out = self.h(action="tsp",
                     points=[[0, 0], [1, 0], [1, 1], [0, 1]])
        self.assertIn("tour cost 4", out)
        self.assertIn("heuristic", out)

    def test_tsp_needs_points_or_matrix(self):
        self.assertIn("refused", self.h(action="tsp"))
        self.assertIn("refused", self.h(action="tsp", points=[[0, 0]]))

    def test_tsp_with_distance_matrix(self):
        out = self.h(action="tsp",
                     distance_matrix=[[0, 1, 2], [1, 0, 1], [2, 1, 0]])
        self.assertIn("tour cost 4", out)

    # -- VRP --------------------------------------------------------------

    def test_vrp_capacity_and_coverage(self):
        out = self.h(action="vrp", points=[[0, 0], [1, 0], [2, 0],
                                           [0, 1]],
                     demands=[4, 4, 4], capacity=10)
        self.assertIn("2 route(s)", out)
        routes = opt._vrp_solve(
            opt._parse_points([[0, 0], [1, 0], [2, 0], [0, 1]]),
            [4, 4, 4], 10)
        self.assertEqual(sorted(sum([r["customers"] for r in routes], [])),
                         [1, 2, 3])
        self.assertTrue(all(r["load"] <= 10 for r in routes))

    def test_vrp_refuses_a_demand_above_capacity(self):
        out = self.h(action="vrp", points=[[0, 0], [1, 0]],
                     demands=[11], capacity=10)
        self.assertIn("refused", out)

    # -- bin packing / knapsack ------------------------------------------

    def test_binpack_ffd_known_instance(self):
        out = self.h(action="binpack", items=[6, 6, 5, 5, 4, 4],
                     capacity=10)
        self.assertIn("3 bin(s)", out)
        bins = opt._binpack([6, 6, 5, 5, 4, 4], 10, "ffd")
        self.assertTrue(all(b["load"] <= 10 for b in bins))

    def test_binpack_refuses_an_oversize_item(self):
        out = self.h(action="binpack", items=[11], capacity=10)
        self.assertIn("refused", out)

    def test_knapsack_known_instance(self):
        out = self.h(action="knapsack", weights=[2, 3, 4, 5],
                     values=[3, 4, 5, 6], capacity=5)
        self.assertIn("max value 7", out)
        self.assertIn("#0", out)
        self.assertIn("#1", out)

    def test_knapsack_matches_brute_force(self):
        random.seed(3)
        for _trial in range(5):
            n = random.randint(2, 7)
            ws = [random.randint(1, 5) for _i in range(n)]
            vs = [random.randint(1, 9) for _i in range(n)]
            cap = random.randint(1, 10)
            val, _ch = opt._knapsack(ws, vs, cap)
            best = max(sum(vs[i] for i in s)
                       for r in range(n + 1)
                       for s in itertools.combinations(range(n), r)
                       if sum(ws[i] for i in s) <= cap)
            self.assertEqual(val, best)

    # -- shortest path / assignment / max flow ----------------------------

    def test_shortest_path_known_graph(self):
        out = self.h(action="shortest_path",
                     edges=[[0, 1, 4], [0, 2, 1], [2, 1, 2], [1, 3, 1],
                            [2, 3, 5]], source=0, target=3)
        self.assertIn("distance 4", out)
        self.assertIn("0 -> 2 -> 1 -> 3", out)

    def test_shortest_path_refuses_negative_weights(self):
        out = self.h(action="shortest_path", edges=[[0, 1, -1]],
                     source=0, target=1)
        self.assertIn("refused", out)

    def test_assignment_matches_brute_force(self):
        random.seed(5)
        for _trial in range(5):
            n = random.randint(2, 4)
            m = random.randint(n, 5)
            cost = [[random.randint(0, 15) for _ in range(m)]
                    for _ in range(n)]
            total, _a = opt._hungarian(cost)
            best = min(sum(cost[i][cols[i]] for i in range(n))
                       for cols in itertools.permutations(range(m), n))
            self.assertAlmostEqual(total, best)

    def test_max_flow_known_instance(self):
        out = self.h(action="max_flow",
                     edges=[[0, 1, 3], [0, 2, 2], [1, 2, 1], [1, 3, 2],
                            [2, 3, 3]], source=0, target=3)
        self.assertIn("max flow 5", out)

    # -- LP ----------------------------------------------------------------

    def test_lp_optimal(self):
        out = self.h(action="lp", objective=[3, 2],
                     constraints=[{"coeffs": [1, 1], "sense": "<=",
                                   "rhs": 4},
                                  {"coeffs": [2, 1], "sense": "<=",
                                   "rhs": 5}])
        self.assertIn("optimal value 9", out)
        self.assertIn("x1 = 1", out)
        self.assertIn("x2 = 3", out)

    def test_lp_infeasible_and_unbounded(self):
        out = self.h(action="lp", objective=[1],
                     constraints=[{"coeffs": [1], "sense": "<=",
                                   "rhs": 1},
                                  {"coeffs": [1], "sense": ">=",
                                   "rhs": 2}])
        self.assertIn("infeasible", out)
        out = self.h(action="lp", objective=[1, 1],
                     constraints=[{"coeffs": [1, -1], "sense": "<=",
                                   "rhs": 1}])
        self.assertIn("unbounded", out)

    # -- constraints -------------------------------------------------------

    def test_constraints_send_more_money(self):
        variables = {c: list(range(10)) for c in "SENDMORY"}
        variables["S"] = list(range(1, 10))
        variables["M"] = list(range(1, 10))
        terms = {"S": 1000, "E": 91, "N": -90, "D": 1, "M": -9000,
                 "O": -900, "R": 10, "Y": -1}
        out = self.h(action="constraints", variables=variables,
                     constraints=[{"type": "all_different",
                                   "vars": list("SENDMORY")},
                                  {"type": "linear", "terms": terms,
                                   "sense": "=", "rhs": 0}])
        self.assertIn("solved", out)
        self.assertIn("S = 9", out)
        self.assertIn("NOT CP-SAT", out)

    def test_constraints_no_solution(self):
        out = self.h(action="constraints",
                     variables={"x": [5, 6], "y": [5, 6]},
                     constraints=[{"type": "linear",
                                   "terms": {"x": 1, "y": 1},
                                   "sense": "=", "rhs": 1}])
        self.assertIn("no solution", out)

    # -- ledger ------------------------------------------------------------

    def test_ledger_records_and_verifies(self):
        self.h(action="tsp", points=[[0, 0], [1, 0], [1, 1], [0, 1]])
        self.h(action="knapsack", weights=[2, 3], values=[3, 4],
               capacity=5)
        self.assertTrue(self.store.verify_chain())
        self.assertEqual(len(self.store.list_events(limit=100)), 2)
        out = self.h(action="events", mode="verify")
        self.assertIn("verifies", out)
        out = self.h(action="status")
        self.assertIn("2 solve(s) recorded", out)
        self.assertIn("tsp: 1", out)

    def test_ledger_tamper_is_caught(self):
        self.h(action="tsp", points=[[0, 0], [1, 0], [1, 1], [0, 1]])
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE events SET hash = '00' WHERE id = 1")
        conn.commit()
        conn.close()
        self.assertFalse(self.store.verify_chain())
        out = self.h(action="events", mode="verify")
        self.assertIn("does NOT verify", out)


if __name__ == "__main__":
    unittest.main()
