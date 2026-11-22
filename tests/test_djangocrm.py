"""Suite for the deals pack (R19-8, Django-CRM/Django-CRM inspiration).

Covers: the tool is registered (750 total, 21 properties, required
action, additionalProperties False); the licence marker accepts
EITHER state (PROPOSED or Parent-ruled — the parent flips it after
the worker's run); the census pins (750/382/368/162, params 2045,
grouped 748, closed 749, verified 302); the gate (free by design,
app-state — not in the confirm set, in the free set, every action
free, unknown actions refused by the handler); the pipeline / deal /
ticket flows (seeded Sales pipeline, probability auto-set from the
stage, aging, the close validation, the SLA defaults and the Pending
pause); the store (reads never create the file, the ledger verifies,
tampering breaks the chain); the bounds; no foreign imports
(stdlib + tree config only).
"""

import ast
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest

import core.deals as dl
from core.funnel.tools_registry import tools as _tools

_HERE = os.path.dirname(os.path.abspath(__file__))
_TREE = os.path.dirname(_HERE)


class _DealsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="djangocrm_test_")
        self._old_data_dir = dl.DATA_DIR
        dl.DATA_DIR = self.tmp
        self.store = dl._DealsStore(
            path=os.path.join(self.tmp, "deals.sqlite3"))
        # A fake erp store with one customer (the real erp schema
        # for the customers table, id + name only).
        erp = sqlite3.connect(os.path.join(self.tmp, "erp.sqlite3"))
        erp.execute("CREATE TABLE customers (id TEXT PRIMARY KEY,"
                    " name TEXT NOT NULL, email TEXT, phone TEXT,"
                    " company TEXT, created REAL NOT NULL)")
        erp.execute("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)",
                    ("CUS-0001", "Acme Corp", None, None, None,
                     time.time()))
        erp.commit()
        erp.close()

    def tearDown(self):
        dl.DATA_DIR = self._old_data_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _f(self, **kw):
        kw.setdefault("_store", self.store)
        return dl._handler(**kw)

    def _deal(self, name="Acme rollout", customer="CUS-0001", **kw):
        kw.setdefault("amount", 500000)
        out = self._f(action="deal", kind="add", name=name,
                      customer=customer, **kw)
        self.assertEqual(out["status"], "ok", out)
        return out["deal"]["id"]

    def _ticket(self, name="Login fails", **kw):
        kw.setdefault("priority", "High")
        kw.setdefault("case_type", "Incident")
        out = self._f(action="ticket", kind="add", name=name, **kw)
        self.assertEqual(out["status"], "ok", out)
        return out["ticket"]["id"]


class TestRegistry(_DealsBase):
    def test_deals_registered(self):
        decls = _tools.get_declarations()
        names = [d["name"] for d in decls]
        self.assertEqual(len(decls), 769)  # INTENTIONAL — R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertEqual(len(set(names)), 769)  # INTENTIONAL — R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertIn("deals", names)
        self.assertEqual(names.count("deals"), 1)

    def test_deals_declaration(self):
        decls = _tools.get_declarations()
        d = [x for x in decls if x["name"] == "deals"][0]
        self.assertEqual(len(d["parameters"]["properties"]), 21)
        self.assertEqual(d["parameters"]["required"], ["action"])
        self.assertIs(d["parameters"]["additionalProperties"], False)
        self.assertEqual(
            d["parameters"]["properties"]["action"]["enum"],
            ["pipeline", "deal", "ticket", "status", "events"])

    def test_params_total(self):
        decls = _tools.get_declarations()
        total = sum(len(d["parameters"]["properties"]) for d in decls)
        self.assertEqual(total, 2121)  # INTENTIONAL — R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_closed_total(self):
        decls = _tools.get_declarations()
        closed = sum(1 for d in decls
                     if d["parameters"].get("additionalProperties")
                     is False)
        self.assertEqual(closed, 768)  # INTENTIONAL — R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_grouped_total(self):
        from core.catalogue_snapshot import GROUP_MEMBERS
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 766)  # INTENTIONAL — R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_deals_grouped_everyday_skills(self):
        from core.catalogue_snapshot import GROUP_MEMBERS
        self.assertIn("deals", GROUP_MEMBERS["Everyday skills"])
        # The single catalogue source (core/catalogue_groups_data.py,
        # GAPS-23 F-V7) is checked as text instead (the R19-4
        # vidbee precedent): find the "Everyday skills" tuple in
        # GROUP_MEMBERS and look inside it.
        text = open(os.path.join(_TREE, "core",
                                 "catalogue_groups_data.py"),
                    encoding="utf-8").read()
        start = text.index('"Everyday skills": (')
        end = text.index("\n    ),", start)
        self.assertIn('"deals",', text[start:end])

    def test_licence_marker_either_state(self):
        """The licence test accepts EITHER marker state (the parent
        flips it after the worker's run)."""
        text = open(os.path.join(_TREE, "core", "deals.py"),
                    encoding="utf-8").read()
        self.assertTrue(
            "PROPOSED — parent rules" in text
            or "Parent-ruled (licence, 2026-11-12)" in text,
            "the licence marker must be PROPOSED or Parent-ruled")
        self.assertIn("MIT", text)
        self.assertIn("Copyright (c) 2017 MicroPyramid", text)
        self.assertIn("Copyright (c) 2026 RunCode Inc.", text)
        self.assertIn("1,101 bytes, 22 lines", text)


class TestGate(_DealsBase):
    def test_deals_is_free_by_design(self):
        from core.funnel.tools_gate_free import _FREE_BY_DESIGN
        self.assertEqual(_FREE_BY_DESIGN.get("deals"), "app-state")

    def test_deals_not_in_confirm_set(self):
        from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
        self.assertNotIn("deals", _CONFIRM_GATE_TOOLS)
        self.assertEqual(len(_CONFIRM_GATE_TOOLS), 169)  # INTENTIONAL — SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169

    def test_all_actions_are_free(self):
        from core.funnel.tools_gate import _needs_confirmation
        for action in ("pipeline", "deal", "ticket", "status",
                       "events"):
            self.assertFalse(
                _needs_confirmation("deals", {"action": action}),
                action)

    def test_unknown_action_refused_by_handler(self):
        out = self._f(action="bogus")
        self.assertEqual(out["status"], "error")
        self.assertIn("unknown action", out["message"])
        out = self._f()
        self.assertEqual(out["status"], "error")

    def test_readclass(self):
        from core.funnel.tools_gate_readclass import _is_read_action
        self.assertTrue(_is_read_action("deals",
                                        {"action": "status"}))
        self.assertTrue(_is_read_action("deals",
                                        {"action": "events"}))
        for action in ("pipeline", "deal", "ticket"):
            self.assertTrue(_is_read_action(
                "deals", {"action": action, "kind": "list"}))
            self.assertTrue(_is_read_action(
                "deals", {"action": action, "kind": "get"}))
            self.assertFalse(_is_read_action(
                "deals", {"action": action, "kind": "add"}))
            self.assertFalse(_is_read_action(
                "deals", {"action": action, "kind": "remove"}))
        self.assertFalse(_is_read_action(
            "deals", {"action": "deal", "kind": "move"}))
        self.assertFalse(_is_read_action(
            "deals", {"action": "ticket", "kind": "respond"}))


class TestPipeline(_DealsBase):
    def test_pipeline_list_empty_store(self):
        out = self._f(action="pipeline", kind="list")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["pipelines"], [])
        self.assertEqual(out["count"], 0)

    def test_first_deal_seeds_sales_pipeline(self):
        did = self._deal()
        out = self._f(action="pipeline", kind="list")
        self.assertEqual(out["count"], 1)
        pipe = out["pipelines"][0]
        self.assertEqual(pipe["id"], "PL-0001")
        self.assertEqual(pipe["name"], "Sales")
        self.assertTrue(pipe["is_default"])
        self.assertEqual(len(pipe["stages"]), 6)
        self.assertEqual(
            [s["code"] for s in pipe["stages"]],
            ["PROSPECTING", "QUALIFICATION", "PROPOSAL",
             "NEGOTIATION", "CLOSED_WON", "CLOSED_LOST"])
        out = self._f(action="deal", kind="get", deal=did)
        self.assertEqual(out["deal"]["pipeline_id"], "PL-0001")

    def test_pipeline_add_list_get_set_default(self):
        out = self._f(action="pipeline", kind="add", name="Partners")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["pipeline"]["id"], "PL-0001")
        self.assertTrue(out["pipeline"]["is_default"])
        self.assertEqual(len(out["pipeline"]["stages"]), 6)
        out = self._f(action="pipeline", kind="add", name="Direct")
        self.assertEqual(out["pipeline"]["id"], "PL-0002")
        self.assertFalse(out["pipeline"]["is_default"])
        out = self._f(action="pipeline", kind="list")
        self.assertEqual(out["count"], 2)
        out = self._f(action="pipeline", kind="get",
                      pipeline="Direct")
        self.assertEqual(out["pipeline"]["id"], "PL-0002")
        out = self._f(action="pipeline", kind="set_default",
                      pipeline="PL-0002")
        self.assertEqual(out["status"], "ok")
        out = self._f(action="pipeline", kind="get",
                      pipeline="PL-0002")
        self.assertTrue(out["pipeline"]["is_default"])

    def test_pipeline_duplicate_and_unknown_refused(self):
        self._f(action="pipeline", kind="add", name="Partners")
        out = self._f(action="pipeline", kind="add", name="Partners")
        self.assertEqual(out["status"], "error")
        self.assertIn("already exists", out["message"])
        out = self._f(action="pipeline", kind="get", pipeline="Nope")
        self.assertEqual(out["status"], "error")
        self.assertIn("no pipeline", out["message"])
        out = self._f(action="pipeline", kind="bogus")
        self.assertEqual(out["status"], "error")

    def test_pipeline_remove_refused_while_deals_exist(self):
        self._deal()
        out = self._f(action="pipeline", kind="remove",
                      pipeline="Sales")
        self.assertEqual(out["status"], "error")
        self.assertIn("1 deal", out["message"])

    def test_pipeline_remove_empty(self):
        self._f(action="pipeline", kind="add", name="Partners")
        out = self._f(action="pipeline", kind="remove",
                      pipeline="Partners")
        self.assertEqual(out["status"], "ok")
        out = self._f(action="pipeline", kind="list")
        self.assertEqual(out["count"], 0)


class TestDeal(_DealsBase):
    def test_deal_add_list_get_remove(self):
        did = self._deal(name="Acme rollout", amount=500000,
                         currency="USD", type="New business",
                         lead_source="Referral")
        out = self._f(action="deal", kind="list")
        self.assertEqual(out["count"], 1)
        got = out["deals"][0]
        self.assertEqual(got["name"], "Acme rollout")
        self.assertEqual(got["customer"], "Acme Corp")
        self.assertEqual(got["stage"], "PROSPECTING")
        self.assertEqual(got["probability"], 10)
        self.assertEqual(got["amount_cents"], 500000)
        self.assertEqual(got["amount"], "5,000.00")
        self.assertEqual(got["aging"], "green")
        out = self._f(action="deal", kind="get", deal=did)
        self.assertEqual(out["deal"]["type"], "New business")
        self.assertEqual(out["deal"]["lead_source"], "Referral")
        out = self._f(action="deal", kind="remove", deal=did)
        self.assertEqual(out["status"], "ok")
        out = self._f(action="deal", kind="list")
        self.assertEqual(out["count"], 0)

    def test_deal_explicit_probability_wins(self):
        did = self._deal(probability=42)
        out = self._f(action="deal", kind="get", deal=did)
        self.assertEqual(out["deal"]["probability"], 42)

    def test_deal_move_resets_probability(self):
        did = self._deal()
        out = self._f(action="deal", kind="move", deal=did,
                      stage="NEGOTIATION")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["deal"]["stage"], "NEGOTIATION")
        self.assertEqual(out["deal"]["probability"], 75)
        out = self._f(action="deal", kind="move", deal=did,
                      stage="BOGUS")
        self.assertEqual(out["status"], "error")
        out = self._f(action="deal", kind="move", deal=did)
        self.assertEqual(out["status"], "error")
        self.assertIn("stage is required", out["message"])

    def test_deal_close_won_needs_amount(self):
        did = self._deal(name="No-amount deal", amount=None)
        out = self._f(action="deal", kind="close", deal=did,
                      outcome="won")
        self.assertEqual(out["status"], "error")
        self.assertIn("amount is required for a won deal",
                      out["message"])
        out = self._f(action="deal", kind="close", deal=did,
                      outcome="won", amount=1000)
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["deal"]["stage"], "CLOSED_WON")
        self.assertEqual(out["deal"]["probability"], 100)
        self.assertIsNotNone(out["deal"]["closed_on"])

    def test_deal_close_lost_needs_no_amount(self):
        did = self._deal(name="No-amount deal", amount=None)
        out = self._f(action="deal", kind="close", deal=did,
                      outcome="lost")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["deal"]["stage"], "CLOSED_LOST")
        self.assertEqual(out["deal"]["probability"], 0)
        self.assertIsNotNone(out["deal"]["closed_on"])

    def test_deal_close_outcome_required(self):
        did = self._deal()
        out = self._f(action="deal", kind="close", deal=did)
        self.assertEqual(out["status"], "error")
        self.assertIn("outcome is required", out["message"])
        out = self._f(action="deal", kind="close", deal=did,
                      outcome="maybe")
        self.assertEqual(out["status"], "error")

    def test_deal_customer_validation(self):
        out = self._f(action="deal", kind="add", name="Ghost",
                      customer="CUS-9999")
        self.assertEqual(out["status"], "error")
        self.assertIn("no erp customer", out["message"])
        out = self._f(action="deal", kind="add", name="No customer")
        self.assertEqual(out["status"], "error")
        self.assertIn("customer is required", out["message"])

    def test_deal_add_refuses_without_erp_store(self):
        os.remove(os.path.join(self.tmp, "erp.sqlite3"))
        out = self._f(action="deal", kind="add", name="No ERP",
                      customer="CUS-0001")
        self.assertEqual(out["status"], "error")
        self.assertIn("no erp store yet", out["message"])

    def test_deal_aging_red_after_expected_days(self):
        did = self._deal()
        conn = sqlite3.connect(
            os.path.join(self.tmp, "deals.sqlite3"))
        conn.execute("UPDATE deals SET stage_changed_at = ?"
                     " WHERE id = ?",
                     (time.time() - 22 * 86400, did))
        conn.commit()
        conn.close()
        out = self._f(action="deal", kind="get", deal=did)
        self.assertEqual(out["deal"]["aging"], "red")
        self.assertEqual(out["deal"]["days_in_stage"], 22)

    def test_deal_bad_amount_and_probability_refused(self):
        for kw in ({"amount": -1}, {"amount": 1000000001},
                   {"probability": -1}, {"probability": 101},
                   {"amount": "rich"}, {"probability": "high"}):
            out = self._f(action="deal", kind="add", name="X",
                          customer="CUS-0001", **kw)
            self.assertEqual(out["status"], "error", kw)

    def test_deal_bad_currency_refused(self):
        out = self._f(action="deal", kind="add", name="X",
                      customer="CUS-0001", currency="USDD")
        self.assertEqual(out["status"], "error")
        out = self._f(action="deal", kind="add", name="X",
                      customer="CUS-0001", currency="usd")
        self.assertEqual(out["status"], "ok", out)


class TestTicket(_DealsBase):
    def test_ticket_add_list_get(self):
        tid = self._ticket(name="Login fails", priority="High",
                           case_type="Incident",
                           description="Cannot sign in.")
        out = self._f(action="ticket", kind="list")
        self.assertEqual(out["count"], 1)
        got = out["tickets"][0]
        self.assertEqual(got["name"], "Login fails")
        self.assertEqual(got["status"], "New")
        self.assertEqual(got["priority"], "High")
        self.assertEqual(got["sla_first_response_hours"], 4)
        self.assertEqual(got["sla_resolution_hours"], 24)
        out = self._f(action="ticket", kind="get", ticket=tid)
        self.assertEqual(out["ticket"]["description"],
                         "Cannot sign in.")

    def test_ticket_sla_defaults_by_priority(self):
        for priority, first, resolution in (
                ("Low", 24, 72), ("Normal", 8, 48),
                ("High", 4, 24), ("Urgent", 1, 4)):
            tid = self._ticket(name="SLA " + priority,
                               priority=priority)
            out = self._f(action="ticket", kind="get", ticket=tid)
            self.assertEqual(
                out["ticket"]["sla_first_response_hours"], first,
                priority)
            self.assertEqual(
                out["ticket"]["sla_resolution_hours"], resolution,
                priority)

    def test_ticket_respond_assigns_and_notes(self):
        tid = self._ticket()
        out = self._f(action="ticket", kind="respond", ticket=tid,
                      note="Have you tried resetting?")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["ticket"]["status"], "Assigned")
        self.assertIsNotNone(out["ticket"]["first_response_at"])
        self.assertEqual(out["ticket"]["notes"], 1)
        out = self._f(action="ticket", kind="respond", ticket=tid,
                      note="Second note.")
        self.assertEqual(out["ticket"]["notes"], 2)

    def test_ticket_pause_and_resume(self):
        tid = self._ticket()
        out = self._f(action="ticket", kind="set_status", ticket=tid,
                      status="Pending")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["ticket"]["status"], "Pending")
        conn = sqlite3.connect(
            os.path.join(self.tmp, "deals.sqlite3"))
        paused_at = conn.execute(
            "SELECT sla_paused_at FROM tickets WHERE id = ?",
            (tid,)).fetchone()[0]
        self.assertIsNotNone(paused_at)
        conn.execute("UPDATE tickets SET sla_paused_at = ?"
                     " WHERE id = ?",
                     (time.time() - 3600, tid))
        conn.commit()
        conn.close()
        out = self._f(action="ticket", kind="set_status", ticket=tid,
                      status="Assigned")
        self.assertEqual(out["status"], "ok", out)
        paused = out["ticket"]["sla_paused_seconds"]
        self.assertGreaterEqual(paused, 3500)
        self.assertLessEqual(paused, 3700)

    def test_ticket_close_reopen(self):
        tid = self._ticket()
        out = self._f(action="ticket", kind="close", ticket=tid,
                      status="Closed")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["ticket"]["status"], "Closed")
        self.assertIsNotNone(out["ticket"]["closed_on"])
        self.assertIsNotNone(out["ticket"]["resolved_at"])
        out = self._f(action="ticket", kind="reopen", ticket=tid)
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["ticket"]["status"], "Assigned")
        self.assertIsNone(out["ticket"]["closed_on"])
        self.assertIsNone(out["ticket"]["resolved_at"])
        out = self._f(action="ticket", kind="close", ticket=tid,
                      status="Rejected")
        self.assertEqual(out["ticket"]["status"], "Rejected")

    def test_ticket_bad_status_and_priority_refused(self):
        tid = self._ticket()
        out = self._f(action="ticket", kind="set_status", ticket=tid,
                      status="Bogus")
        self.assertEqual(out["status"], "error")
        out = self._f(action="ticket", kind="close", ticket=tid,
                      status="New")
        self.assertEqual(out["status"], "error")
        self.assertIn("close takes a terminal status",
                      out["message"])
        out = self._f(action="ticket", kind="add", name="X",
                      priority="Critical")
        self.assertEqual(out["status"], "error")
        out = self._f(action="ticket", kind="add", name="X",
                      case_type="Feature")
        self.assertEqual(out["status"], "error")

    def test_ticket_customer_optional(self):
        tid = self._ticket(name="No customer ticket",
                           customer=None)
        out = self._f(action="ticket", kind="get", ticket=tid)
        self.assertIsNone(out["ticket"]["customer_id"])


class TestStatusEvents(_DealsBase):
    def test_status_counts(self):
        self._deal(name="Won deal")
        self._deal(name="Lost deal", amount=None)
        self._deal(name="Open deal")
        out = self._f(action="deal", kind="list")
        ids = [d["id"] for d in out["deals"]]
        self._f(action="deal", kind="close", deal=ids[0],
                outcome="won")
        self._f(action="deal", kind="close", deal=ids[1],
                outcome="lost")
        self._ticket()
        out = self._f(action="status")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["pipelines"], 1)
        self.assertEqual(out["deals"], 3)
        self.assertEqual(out["deals_open"], 1)
        self.assertEqual(out["deals_won"], 1)
        self.assertEqual(out["deals_lost"], 1)
        self.assertEqual(out["tickets"], 1)
        self.assertEqual(out["tickets_by_status"], {"New": 1})
        self.assertTrue(out["chain_ok"])

    def test_status_empty_store(self):
        out = self._f(action="status")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["pipelines"], 0)
        self.assertEqual(out["deals"], 0)
        self.assertEqual(out["tickets"], 0)
        self.assertTrue(out["chain_ok"])

    def test_events_list_and_verify(self):
        self._deal()
        self._ticket()
        out = self._f(action="events", kind="list")
        self.assertEqual(out["status"], "ok")
        self.assertGreaterEqual(out["count"], 3)
        kinds = [e["kind"] for e in out["events"]]
        self.assertIn("pipeline_seed", kinds)
        self.assertIn("deal_add", kinds)
        self.assertIn("ticket_add", kinds)
        out = self._f(action="events", kind="verify")
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["ok"])
        out = self._f(action="events", kind="bogus")
        self.assertEqual(out["status"], "error")

    def test_reads_never_create_the_store(self):
        path = os.path.join(self.tmp, "deals.sqlite3")
        self.assertFalse(os.path.exists(path))
        for kw in ({"action": "status"},
                   {"action": "pipeline", "kind": "list"},
                   {"action": "deal", "kind": "list"},
                   {"action": "ticket", "kind": "list"},
                   {"action": "events", "kind": "list"}):
            out = self._f(**kw)
            self.assertEqual(out["status"], "ok", kw)
        self.assertFalse(os.path.exists(path))

    def test_tampered_ledger_fails_verification(self):
        self._deal()
        ok, count = self.store.verify_chain()
        self.assertTrue(ok)
        self.assertGreaterEqual(count, 2)
        conn = sqlite3.connect(
            os.path.join(self.tmp, "deals.sqlite3"))
        conn.execute("UPDATE events SET hash = ? WHERE id = 1",
                     ("0" * 64,))
        conn.commit()
        conn.close()
        ok, _n = self.store.verify_chain()
        self.assertFalse(ok)
        out = self._f(action="events", kind="verify")
        self.assertEqual(out["status"], "error")

    def test_limit_bounds(self):
        # The limit is validated on the store path, so the store
        # must exist first (a missing store answers empty without
        # validating it).
        self._deal()
        for limit in (0, 101, "many"):
            out = self._f(action="deal", kind="list", limit=limit)
            self.assertEqual(out["status"], "error", limit)
        out = self._f(action="deal", kind="list", limit=1)
        self.assertEqual(out["status"], "ok")

    def test_no_foreign_imports(self):
        """Only stdlib + the tree's own config may be imported."""
        text = open(os.path.join(_TREE, "core", "deals.py"),
                    encoding="utf-8").read()
        tree = ast.parse(text)
        allowed = {"hashlib", "json", "math", "re", "sqlite3",
                   "threading", "time", "datetime", "pathlib"}
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
