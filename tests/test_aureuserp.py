"""R19-3 aureuserp/aureuserp rebuild — a unified local business
record in own stdlib code (customers, leads, products, stock derived
from the movement ledger, sales/purchase orders, invoices, payments,
employees, projects) over a hash-chained ledger.

Every behavioural test drives a temp SQLite store (the handler's
_store seam); nothing here touches the production store, the
network, or a live clock beyond the module's own time.time() /
date.today() calls. No PHP code, no Laravel, no Filament, no MySQL,
no wrapper, no port. Invoices and payments are records the owner
made — the money moved outside VYUK, the tool never takes payment,
never computes tax, never keeps payroll.
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".pkgroot"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import erp
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _needs_confirmation,
)
from core.funnel.tools_registry import tools as _registry

TREE = Path(__file__).resolve().parent.parent


class ErpTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "erp.db")
        self.store = erp._ErpStore(path=self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def h(self, **kw):
        return erp.erp(_store=self.store, **kw)

    def _seed_customer_product(self, price=1000, qty=10):
        out = self.h(action="customer", kind="add", name="Acme Corp")
        self.assertIn("CUS-0001", out)
        out = self.h(action="product", kind="add", name="Widget",
                     price_cents=price, quantity=qty)
        self.assertIn("PRD-0001", out)
        return "CUS-0001", "PRD-0001"

    # -- registry / census / catalogue / classification -----------------

    def test_registry_total_is_758(self):  # INTENTIONAL — R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertEqual(len(_registry.get_declarations()), 758)  # INTENTIONAL — R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing

    def test_registry_params_total_is_2079(self):  # INTENTIONAL — R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; GAPS-20 loopback-meeting pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan
        total = sum(len(d.get("parameters", {}).get("properties", {}))
                    for d in _registry.get_declarations())
        self.assertEqual(total, 2079)  # INTENTIONAL — R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan

    def test_catalogue_grouped_is_754(self):  # INTENTIONAL — R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 755)  # INTENTIONAL — R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing

    def test_registry_closed_is_757(self):  # INTENTIONAL — R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing
        closed = sum(1 for d in _registry.get_declarations()
                     if d.get("parameters", {}).get("additionalProperties")
                     is False)
        self.assertEqual(closed, 757)  # INTENTIONAL — R19-3 aureuserp pack (2026-11-12): 743 -> 744 — erp added (additionalProperties False by the registry default, so the new tool is closed); R19-4 vidbee pack (2026-11-12): 744 -> 745 — media_chat added (additionalProperties False by the registry default, so the new tool is closed); R19-5 invoify pack (2026-11-12): 745 -> 746 — invoice added (additionalProperties False by the registry default, so the new tool is closed); R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing

    def test_erp_in_everyday_skills(self):
        self.assertIn("erp", GROUP_MEMBERS["Everyday skills"])

    def test_erp_has_23_properties(self):
        decl = [d for d in _registry.get_declarations()
                if d["name"] == "erp"]
        self.assertEqual(len(decl), 1)
        self.assertEqual(len(decl[0]["parameters"]["properties"]), 23)
        self.assertEqual(decl[0]["parameters"]["required"], ["action"])

    def test_erp_is_free_by_design(self):
        self.assertEqual(_FREE_BY_DESIGN.get("erp"), "app-state")
        self.assertNotIn("erp", _CONFIRM_GATE_TOOLS)
        self.assertFalse(_needs_confirmation("erp", {}))

    def test_classification_snapshot(self):
        snap = json.load(open(TREE / "tests" / "_classification_snapshot.json"))
        entry = snap["erp"]
        self.assertEqual(entry["free"], "app-state")
        self.assertIs(entry["confirm"], False)
        self.assertIs(entry["read"], False)

    def test_licence_marker_is_proposed_or_parent_ruled(self):
        text = open(TREE / "core" / "erp.py",
                    encoding="utf-8").read()
        self.assertTrue("PROPOSED — parent rules" in text
                        or "Parent-ruled" in text)

    def test_no_network_or_foreign_imports(self):
        text = open(TREE / "core" / "erp.py",
                    encoding="utf-8").read()
        # Import patterns, not words: "laravel" / "filament" /
        # "mysql" DO appear in the docstring as the STOP list of
        # what is NOT built — the check is that no such library is
        # imported or used.
        for banned in ("import urllib", "from urllib", "import requests",
                       "import socket", "http.client", "import subprocess",
                       "os.system", "Popen", "import laravel",
                       "from laravel", "import filament",
                       "from filament", "import mysql", "from mysql",
                       "import pymysql", "sqlalchemy", "streamlit"):
            self.assertNotIn(banned, text)

    def test_no_payroll_tax_or_money_movement_action(self):
        self.assertNotIn("payroll", erp._ACTIONS)
        self.assertNotIn("tax", erp._ACTIONS)
        self.assertNotIn("charge", erp._ACTIONS)
        self.assertNotIn("refund", erp._ACTIONS)

    # -- store guard -----------------------------------------------------

    def test_reads_do_not_create_the_store_file(self):
        self.assertEqual(self.h(action="status"),
                         "No business records yet — the ledger is empty.")
        self.assertFalse(os.path.exists(self.db))
        self.assertEqual(self.h(action="events"),
                         "No business records yet — the ledger is empty.")
        self.assertFalse(os.path.exists(self.db))
        self.assertEqual(self.h(action="customer", kind="list"),
                         "No business records yet — the ledger is empty.")
        self.assertFalse(os.path.exists(self.db))

    # -- customers ---------------------------------------------------------

    def test_customer_add_list_get(self):
        out = self.h(action="customer", kind="add", name="Acme Corp",
                     email="a@acme.test", company="Acme")
        self.assertIn("CUS-0001", out)
        out = self.h(action="customer", kind="list")
        self.assertIn("CUS-0001", out)
        self.assertIn("Acme Corp", out)
        out = self.h(action="customer", kind="get", id="CUS-0001")
        self.assertIn("Acme Corp", out)
        self.assertIn("open balance: 0.00", out)

    def test_customer_remove_refused_with_orders(self):
        cid, _pid = self._seed_customer_product()
        out = self.h(action="sales_order", kind="create",
                     customer_id=cid,
                     lines=[{"product_id": "PRD-0001", "quantity": 1}])
        self.assertIn("SO-0001", out)
        out = self.h(action="customer", kind="remove", id=cid)
        self.assertIn("refused", out)
        self.assertIn("1 sales order(s)", out)

    def test_customer_remove_after_no_orders(self):
        out = self.h(action="customer", kind="add", name="Solo")
        self.assertIn("CUS-0001", out)
        out = self.h(action="customer", kind="remove", id="CUS-0001")
        self.assertIn("removed", out)
        out = self.h(action="customer", kind="list")
        self.assertIn("No customers yet.", out)

    # -- leads -------------------------------------------------------------

    def test_lead_convert_creates_customer(self):
        out = self.h(action="lead", kind="add", name="Dana",
                     company="Dana Ltd")
        self.assertIn("LEAD-0001", out)
        out = self.h(action="lead", kind="convert", id="LEAD-0001")
        self.assertIn("converted to customer CUS-0001", out)
        out = self.h(action="customer", kind="get", id="CUS-0001")
        self.assertIn("Dana", out)
        self.assertIn("Dana Ltd", out)

    def test_lead_double_convert_refused(self):
        self.h(action="lead", kind="add", name="Dana")
        self.h(action="lead", kind="convert", id="LEAD-0001")
        out = self.h(action="lead", kind="convert", id="LEAD-0001")
        self.assertIn("refused", out)
        self.assertIn("already converted", out)

    # -- products and stock ------------------------------------------------

    def test_product_add_lists_stock(self):
        out = self.h(action="product", kind="add", name="Widget",
                     price_cents=2500, quantity=7)
        self.assertIn("PRD-0001", out)
        self.assertIn("25.00 each", out)
        self.assertIn("stock 7", out)
        out = self.h(action="product", kind="list")
        self.assertIn("stock 7", out)

    def test_stock_adjust_and_below_zero_refused(self):
        _cid, pid = self._seed_customer_product(qty=10)
        out = self.h(action="stock", kind="adjust", product_id=pid,
                     quantity=-4, reason="damaged")
        self.assertIn("now 6", out)
        out = self.h(action="stock", kind="adjust", product_id=pid,
                     quantity=-7, reason="too many")
        self.assertIn("refused", out)
        self.assertIn("below zero", out)
        out = self.h(action="stock", kind="list", product_id=pid)
        self.assertIn("stock 6", out)
        self.assertIn("-4 — damaged", out)

    def test_product_remove_refused_on_order_lines(self):
        cid, pid = self._seed_customer_product()
        self.h(action="sales_order", kind="create", customer_id=cid,
               lines=[{"product_id": pid, "quantity": 1}])
        out = self.h(action="product", kind="remove", id=pid)
        self.assertIn("refused", out)
        self.assertIn("1 sales order line(s)", out)

    # -- sales orders ------------------------------------------------------

    def test_sales_order_create_fulfil_decrements_stock(self):
        cid, pid = self._seed_customer_product(qty=10)
        out = self.h(action="sales_order", kind="create",
                     customer_id=cid,
                     lines=[{"product_id": pid, "quantity": 3}])
        self.assertIn("SO-0001", out)
        self.assertIn("total 30.00", out)
        out = self.h(action="sales_order", kind="fulfil", id="SO-0001")
        self.assertIn("fulfilled", out)
        out = self.h(action="stock", kind="list", product_id=pid)
        self.assertIn("stock 7", out)

    def test_sales_order_fulfil_shortfall_refused(self):
        cid, pid = self._seed_customer_product(qty=2)
        self.h(action="sales_order", kind="create", customer_id=cid,
               lines=[{"product_id": pid, "quantity": 5}])
        out = self.h(action="sales_order", kind="fulfil", id="SO-0001")
        self.assertIn("refused", out)
        self.assertIn("needs 5 but only 2 in stock", out)

    def test_sales_order_cancel_then_fulfil_refused(self):
        cid, _pid = self._seed_customer_product()
        self.h(action="sales_order", kind="create", customer_id=cid,
               lines=[{"product_id": "PRD-0001", "quantity": 1}])
        out = self.h(action="sales_order", kind="cancel", id="SO-0001")
        self.assertIn("cancelled", out)
        out = self.h(action="sales_order", kind="fulfil", id="SO-0001")
        self.assertIn("refused", out)

    # -- purchase orders ---------------------------------------------------

    def test_purchase_order_receive_increments_stock(self):
        _cid, pid = self._seed_customer_product(qty=4)
        out = self.h(action="purchase_order", kind="create",
                     vendor="Supply Co",
                     lines=[{"product_id": pid, "quantity": 6,
                             "price_cents": 800}])
        self.assertIn("PO-0001", out)
        self.assertIn("total 48.00", out)
        out = self.h(action="purchase_order", kind="receive",
                     id="PO-0001")
        self.assertIn("received", out)
        out = self.h(action="stock", kind="list", product_id=pid)
        self.assertIn("stock 10", out)
        out = self.h(action="purchase_order", kind="receive",
                     id="PO-0001")
        self.assertIn("refused", out)

    def test_purchase_order_requires_supplier_price(self):
        _cid, pid = self._seed_customer_product()
        out = self.h(action="purchase_order", kind="create",
                     vendor="Supply Co",
                     lines=[{"product_id": pid, "quantity": 1}])
        self.assertIn("refused", out)
        self.assertIn("needs a price_cents", out)

    # -- invoices and payments --------------------------------------------

    def test_invoice_from_order_then_payment_pays_it(self):
        cid, pid = self._seed_customer_product(qty=10)
        self.h(action="sales_order", kind="create", customer_id=cid,
               lines=[{"product_id": pid, "quantity": 2}])
        out = self.h(action="invoice", kind="create", order_id="SO-0001",
                     due_date="2099-01-31")
        self.assertIn("INV-0001", out)
        self.assertIn("total 20.00", out)
        self.assertIn("no tax computed", out)
        out = self.h(action="payment", kind="record",
                     invoice_id="INV-0001", amount_cents=2000)
        self.assertIn("PAY-0001", out)
        self.assertIn("balance now 0.00 (paid)", out)
        out = self.h(action="invoice", kind="get", id="INV-0001")
        self.assertIn("paid", out)
        self.assertIn("no tax computed", out)

    def test_invoice_second_for_same_order_refused(self):
        cid, pid = self._seed_customer_product()
        self.h(action="sales_order", kind="create", customer_id=cid,
               lines=[{"product_id": pid, "quantity": 1}])
        self.h(action="invoice", kind="create", order_id="SO-0001")
        out = self.h(action="invoice", kind="create", order_id="SO-0001")
        self.assertIn("refused", out)
        self.assertIn("already has an invoice", out)

    def test_payment_overpay_refused(self):
        cid, pid = self._seed_customer_product()
        self.h(action="sales_order", kind="create", customer_id=cid,
               lines=[{"product_id": pid, "quantity": 1}])
        self.h(action="invoice", kind="create", order_id="SO-0001")
        out = self.h(action="payment", kind="record",
                     invoice_id="INV-0001", amount_cents=1001)
        self.assertIn("refused", out)
        self.assertIn("would overpay", out)
        self.assertIn("remaining 10.00", out)

    def test_invoice_void_after_payment_refused(self):
        cid, pid = self._seed_customer_product()
        self.h(action="sales_order", kind="create", customer_id=cid,
               lines=[{"product_id": pid, "quantity": 1}])
        self.h(action="invoice", kind="create", order_id="SO-0001")
        self.h(action="payment", kind="record", invoice_id="INV-0001",
               amount_cents=500)
        out = self.h(action="invoice", kind="void", id="INV-0001")
        self.assertIn("refused", out)
        self.assertIn("already has 5.00 recorded", out)

    def test_standalone_invoice_and_void(self):
        cid, _pid = self._seed_customer_product()
        out = self.h(action="invoice", kind="create", customer_id=cid,
                     lines=[{"product_id": "PRD-0001", "quantity": 2,
                             "price_cents": 1500}])
        self.assertIn("INV-0001", out)
        self.assertIn("total 30.00", out)
        out = self.h(action="invoice", kind="void", id="INV-0001")
        self.assertIn("voided", out)
        out = self.h(action="payment", kind="record",
                     invoice_id="INV-0001", amount_cents=100)
        self.assertIn("refused", out)
        self.assertIn("is void", out)

    # -- employees and projects --------------------------------------------

    def test_employee_add_list_get(self):
        out = self.h(action="employee", kind="add", name="Ravi",
                     role="clerk", start_date="2026-11-01")
        self.assertIn("EMP-0001", out)
        out = self.h(action="employee", kind="get", id="EMP-0001")
        self.assertIn("Ravi", out)
        self.assertIn("No payroll is kept by this tool.", out)

    def test_project_lifecycle(self):
        cid, _pid = self._seed_customer_product()
        out = self.h(action="project", kind="add", name="Shop fit-out",
                     customer_id=cid, due_date="2026-12-31")
        self.assertIn("PRJ-0001", out)
        self.assertIn("(active)", out)
        out = self.h(action="project", kind="set_status", id="PRJ-0001",
                     status="done")
        self.assertIn("now done", out)
        out = self.h(action="project", kind="list", status="done")
        self.assertIn("PRJ-0001", out)
        out = self.h(action="project", kind="set_status", id="PRJ-0001",
                     status="bogus")
        self.assertIn("refused", out)

    # -- status, events, ledger --------------------------------------------

    def test_status_and_events(self):
        cid, pid = self._seed_customer_product(qty=3)
        self.h(action="sales_order", kind="create", customer_id=cid,
               lines=[{"product_id": pid, "quantity": 1}])
        out = self.h(action="status")
        self.assertIn("customers: 1", out)
        self.assertIn("products: 1", out)
        self.assertIn("sales orders: 1", out)
        self.assertIn("low stock", out)
        self.assertIn("chain verifies", out)
        out = self.h(action="events", kind="verify")
        self.assertIn("verifies", out)
        out = self.h(action="events", kind="list")
        self.assertIn("customer.add", out)
        self.assertIn("sales_order.create", out)

    def test_ledger_tamper_is_caught(self):
        self._seed_customer_product()
        self.assertTrue(self.store.verify_chain())
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE events SET hash = '00' WHERE id = 1")
        conn.commit()
        conn.close()
        self.assertFalse(self.store.verify_chain())
        out = self.h(action="events", kind="verify")
        self.assertIn("does NOT verify", out)

    def test_unknown_action_and_kind_refused(self):
        self.assertIn("refused", self.h(action="frobnicate"))
        self.assertIn("refused",
                      self.h(action="customer", kind="frobnicate"))


if __name__ == "__main__":
    unittest.main()
