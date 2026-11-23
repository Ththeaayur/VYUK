"""Suite for the invoice pack (R19-5, al1abb/invoify inspiration).

Covers: the tool is registered (747 total, 15 properties, required
action, additionalProperties False); the licence marker accepts
EITHER state (PROPOSED or Parent-ruled — the parent flips it after
the worker's run); the census pins (747/382/365/161, params 1987,
grouped 745, closed 746, verified 299); the gate (send is
confirm-gated, the rest free, unknown fails closed); the PDF writer
(all three templates verify structurally, pagination, summary-only,
structural tampering fails); the render flow (order-based and
standalone invoices, supplied-lines total must match, the seller
record, the ledger); the send_email attachments extension (a missing
file refuses before anything is mailed); the store (reads never
create the file); no foreign imports (stdlib + tree config only).
"""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest

import core.invoice as invoice_mod
from core.funnel.tools_registry import tools as _tools

_HERE = os.path.dirname(os.path.abspath(__file__))
_TREE = os.path.dirname(_HERE)


class _InvoiceBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="invoify_test_")
        self._old_data_dir = invoice_mod.DATA_DIR
        invoice_mod.DATA_DIR = self.tmp
        # An erp store in the same temp DATA_DIR, with a customer, a
        # product, a sales order and an invoice (order-based).
        import core.erp as erp_mod
        self._old_erp_data_dir = erp_mod.DATA_DIR
        erp_mod.DATA_DIR = self.tmp
        self.erp_mod = erp_mod
        self.store = invoice_mod._InvoiceStore(
            path=os.path.join(self.tmp, "invoice.sqlite3"))

    def tearDown(self):
        invoice_mod.DATA_DIR = self._old_data_dir
        self.erp_mod.DATA_DIR = self._old_erp_data_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_erp_invoice(self):
        """Create a customer + product + sales order + invoice in the
        temp erp store. Returns the invoice id."""
        erp = self.erp_mod.erp
        out = erp(action="customer", kind="add", name="Priya Sharma",
                  email="priya@example.com", company="Sharma Traders")
        self.assertIn("CUS-0001", out, out)
        out = erp(action="product", kind="add", name="Widget A",
                  price_cents=50000, quantity=10)
        self.assertIn("PRD-0001", out, out)
        out = erp(action="sales_order", kind="create",
                  customer_id="CUS-0001",
                  lines=[{"product_id": "PRD-0001", "quantity": 2}])
        self.assertIn("SO-0001", out, out)
        out = erp(action="invoice", kind="create", order_id="SO-0001",
                  due_date="2026-12-01", note="Thank you.")
        self.assertIn("INV-0001", out, out)
        return "INV-0001"

    def _render(self, **kw):
        kw.setdefault("invoice_id", "INV-0001")
        return invoice_mod.invoice(action="render", _store=self.store,
                                   **kw)


class TestRegistry(_InvoiceBase):
    def test_invoice_registered(self):
        decls = _tools.get_declarations()
        names = [d["name"] for d in decls]
        self.assertEqual(len(decls), 772)  # INTENTIONAL — R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(len(set(names)), 772)  # INTENTIONAL — R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertIn("invoice", names)
        self.assertEqual(names.count("invoice"), 1)

    def test_invoice_declaration(self):
        decls = _tools.get_declarations()
        inv = [d for d in decls if d["name"] == "invoice"][0]
        self.assertEqual(len(inv["parameters"]["properties"]), 15)
        self.assertEqual(inv["parameters"]["required"], ["action"])
        self.assertIs(inv["parameters"]["additionalProperties"], False)

    def test_send_email_gains_attachments(self):
        decls = _tools.get_declarations()
        se = [d for d in decls if d["name"] == "send_email"][0]
        self.assertIn("attachments", se["parameters"]["properties"])
        self.assertEqual(len(se["parameters"]["properties"]), 6)
        self.assertEqual(se["parameters"]["required"],
                         ["to", "subject", "body"])

    def test_params_total(self):
        decls = _tools.get_declarations()
        total = sum(len(d["parameters"]["properties"]) for d in decls)
        self.assertEqual(total, 2126)  # INTENTIONAL — R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_closed_total(self):
        decls = _tools.get_declarations()
        closed = sum(1 for d in decls
                     if d["parameters"].get("additionalProperties")
                     is False)
        self.assertEqual(closed, 771)  # INTENTIONAL — R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_grouped_total(self):
        from core.catalogue_snapshot import GROUP_MEMBERS
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 769)  # INTENTIONAL — R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_licence_marker_either_state(self):
        """The licence test accepts EITHER marker state (the parent
        flips it after the worker's run)."""
        path = os.path.join(_TREE, "core", "invoice.py")
        text = open(path, encoding="utf-8").read()
        self.assertTrue(
            "PROPOSED — parent rules" in text
            or "Parent-ruled (licence, 2026-11-12)" in text,
            "the licence marker must be PROPOSED or Parent-ruled")
        self.assertIn("MIT", text)
        # The copyright line wraps across two lines in the
        # docstring, so collapse whitespace before checking it.
        flat = " ".join(text.split())
        self.assertIn("Copyright (c) 2023 Ali Abbasov", flat)
        self.assertIn("no code adopted", text)


class TestGate(_InvoiceBase):
    def test_send_is_confirm_gated(self):
        from core.funnel.tools_gate import _needs_confirmation
        self.assertTrue(_needs_confirmation(
            "invoice", {"action": "send"}))

    def test_render_template_seller_and_reads_are_free(self):
        from core.funnel.tools_gate import _needs_confirmation
        for action in ("render", "template", "seller", "list", "get",
                       "status", "events"):
            self.assertFalse(_needs_confirmation(
                "invoice", {"action": action}), action)

    def test_unknown_action_gates_fail_closed(self):
        from core.funnel.tools_gate import _needs_confirmation
        self.assertTrue(_needs_confirmation(
            "invoice", {"action": "explode"}))
        self.assertTrue(_needs_confirmation("invoice", {}))

    def test_invoice_in_confirm_set(self):
        from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
        self.assertIn("invoice", _CONFIRM_GATE_TOOLS)
        self.assertEqual(len(_CONFIRM_GATE_TOOLS), 170)  # INTENTIONAL — R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_invoice_not_in_free_set(self):
        from core.funnel.tools_gate_free import _FREE_BY_DESIGN
        self.assertNotIn("invoice", _FREE_BY_DESIGN)

    def test_readclass(self):
        from core.funnel.tools_gate_readclass import _is_read_class
        for action in ("list", "get", "status", "events", "template"):
            self.assertTrue(_is_read_class(
                "invoice", {"action": action}), action)
        for action in ("render", "send", "seller"):
            self.assertFalse(_is_read_class(
                "invoice", {"action": action}), action)


class TestPdfWriter(_InvoiceBase):
    def _data(self, lines=None):
        return {"id": "INV-0001",
                "customer": {"id": "CUS-0001", "name": "Priya Sharma",
                             "email": "priya@example.com",
                             "phone": "98765 43210",
                             "company": "Sharma Traders"},
                "order_id": "SO-0001", "total_cents": 123456,
                "paid_cents": 23456, "status": "open",
                "due_date": "2026-12-01", "note": "Thank you.",
                "created": 0.0,
                "lines": lines if lines is not None else [
                    {"description": "Widget A", "quantity": 2,
                     "unit_cents": 50000, "line_total": 100000}],
                "lines_from": "order"}

    def test_all_templates_verify(self):
        seller = {"name": "Aayur Supplies",
                  "address": "IIT Bombay, Powai, Mumbai",
                  "email": "sales@aayur.example",
                  "phone": "022 1234 5678"}
        for template in ("classic", "modern", "minimal"):
            pages = invoice_mod._layout_invoice(
                self._data(), template, seller)
            path = os.path.join(self.tmp, "t_%s.pdf" % template)
            sha, count = invoice_mod._write_pdf(path, pages)
            raw = open(path, "rb").read()
            ok, verified, err = invoice_mod._verify_pdf_bytes(raw)
            self.assertTrue(ok, "%s: %s" % (template, err))
            self.assertEqual(verified, count)
            self.assertEqual(sha, hashlib.sha256(raw).hexdigest())
            self.assertTrue(raw.startswith(b"%PDF-"))

    def test_pagination(self):
        lines = [{"description": "Item %03d" % i, "quantity": 1,
                  "unit_cents": 100, "line_total": 100}
                 for i in range(120)]
        data = self._data(lines)
        data["total_cents"] = 12000
        pages = invoice_mod._layout_invoice(data, "classic", None)
        path = os.path.join(self.tmp, "long.pdf")
        sha, count = invoice_mod._write_pdf(path, pages)
        raw = open(path, "rb").read()
        ok, verified, err = invoice_mod._verify_pdf_bytes(raw)
        self.assertTrue(ok, err)
        self.assertGreater(count, 1)
        self.assertEqual(verified, count)

    def test_summary_only_invoice(self):
        data = self._data([])
        data["order_id"] = None
        pages = invoice_mod._layout_invoice(data, "classic", None)
        path = os.path.join(self.tmp, "summary.pdf")
        sha, count = invoice_mod._write_pdf(path, pages)
        raw = open(path, "rb").read()
        ok, verified, err = invoice_mod._verify_pdf_bytes(raw)
        self.assertTrue(ok, err)
        self.assertEqual(verified, 1)

    def test_structural_tampering_fails(self):
        pages = invoice_mod._layout_invoice(self._data(), "classic",
                                            None)
        path = os.path.join(self.tmp, "good.pdf")
        invoice_mod._write_pdf(path, pages)
        raw = open(path, "rb").read()
        # Truncated.
        ok, _pages, _err = invoice_mod._verify_pdf_bytes(raw[:-100])
        self.assertFalse(ok)
        # Corrupt an xref offset.
        import re as _re
        data = bytearray(raw)
        m = _re.search(rb"\n(\d{10}) 00000 n \n", bytes(data))
        self.assertIsNotNone(m)
        pos = m.start(1)
        data[pos] = ord("9") if data[pos] != ord("9") else ord("8")
        ok, _pages, _err = invoice_mod._verify_pdf_bytes(bytes(data))
        self.assertFalse(ok)
        # Not a PDF.
        ok, _pages, _err = invoice_mod._verify_pdf_bytes(
            b"hello world, not a pdf")
        self.assertFalse(ok)

    def test_no_foreign_imports(self):
        """The module imports only stdlib + the tree's config."""
        import ast
        path = os.path.join(_TREE, "core", "invoice.py")
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(
                        alias.name.split(".")[0],
                        ("fpdf", "reportlab", "pypdf", "weasyprint",
                         "pdfkit", "borb"),
                        alias.name)
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(
                    (node.module or "").split(".")[0],
                    ("fpdf", "reportlab", "pypdf", "weasyprint",
                     "pdfkit", "borb"),
                    node.module)


class TestRenderFlow(_InvoiceBase):
    def test_render_order_based_invoice(self):
        self._make_erp_invoice()
        out = self._render(template="classic")
        self.assertIn("rendered invoice INV-0001", out, out)
        path = os.path.join(self.tmp, "invoice_renders",
                            "INV-0001.pdf")
        self.assertTrue(os.path.isfile(path), out)
        raw = open(path, "rb").read()
        ok, verified, err = invoice_mod._verify_pdf_bytes(raw)
        self.assertTrue(ok, err)
        self.assertEqual(verified, 1)
        # The render record + the ledger.
        row = self.store.get_row("renders", "RND-0001")
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "INV-0001")
        self.assertEqual(row[4], hashlib.sha256(raw).hexdigest())
        ok, count = self.store.verify_chain()
        self.assertTrue(ok)
        self.assertGreaterEqual(count, 1)

    def test_render_all_templates(self):
        self._make_erp_invoice()
        for template in ("classic", "modern", "minimal"):
            out = invoice_mod.invoice(
                action="render", invoice_id="INV-0001",
                template=template,
                output_path=os.path.join(self.tmp, "%s.pdf" % template),
                _store=self.store)
            self.assertIn("rendered invoice INV-0001", out, out)

    def test_render_unknown_invoice_refused(self):
        out = self._render(invoice_id="INV-9999")
        self.assertTrue(out.startswith("refused:"), out)

    def test_render_bad_template_refused(self):
        self._make_erp_invoice()
        out = self._render(template="fancy")
        self.assertTrue(out.startswith("refused:"), out)

    def test_render_standalone_with_supplied_lines(self):
        erp = self.erp_mod.erp
        out = erp(action="customer", kind="add", name="Ravi Kumar")
        self.assertIn("CUS-0001", out, out)
        out = erp(action="invoice", kind="create",
                  customer_id="CUS-0001",
                  lines=[{"product_id": "PRD-9999", "quantity": 1,
                          "price_cents": 1000}])
        # No such product — the erp create refuses; use a real one.
        self.assertTrue(out.startswith("refused:"), out)
        out = erp(action="product", kind="add", name="Gadget",
                  price_cents=1000, quantity=5)
        self.assertIn("PRD-0001", out, out)
        out = erp(action="invoice", kind="create",
                  customer_id="CUS-0001",
                  lines=[{"product_id": "PRD-0001", "quantity": 3}])
        self.assertIn("INV-0001", out, out)
        # The standalone invoice's lines are not stored — supply
        # them at render time; they must total the invoice total.
        out = self._render(lines=[{"description": "Gadget",
                                   "quantity": 3,
                                   "unit_price_cents": 1000}])
        self.assertIn("rendered invoice INV-0001", out, out)
        out = self._render(lines=[{"description": "Gadget",
                                   "quantity": 1,
                                   "unit_price_cents": 1000}])
        self.assertTrue(out.startswith("refused:"), out)
        self.assertIn("must match the invoice", out)

    def test_render_standalone_without_lines(self):
        erp = self.erp_mod.erp
        erp(action="customer", kind="add", name="Ravi Kumar")
        erp(action="product", kind="add", name="Gadget",
            price_cents=1000, quantity=5)
        erp(action="invoice", kind="create", customer_id="CUS-0001",
            lines=[{"product_id": "PRD-0001", "quantity": 3}])
        out = self._render()
        self.assertIn("rendered invoice INV-0001", out, out)

    def test_template_list_and_get(self):
        out = invoice_mod.invoice(action="template", kind="list",
                                  _store=self.store)
        self.assertIn("classic", out)
        self.assertIn("modern", out)
        self.assertIn("minimal", out)
        out = invoice_mod.invoice(action="template", kind="get",
                                  template="modern", _store=self.store)
        self.assertIn("Modern", out)
        out = invoice_mod.invoice(action="template", kind="get",
                                  template="fancy", _store=self.store)
        self.assertTrue(out.startswith("refused:"), out)

    def test_seller_set_and_get(self):
        out = invoice_mod.invoice(action="seller", kind="get",
                                  _store=self.store)
        self.assertIn("No seller details", out)
        out = invoice_mod.invoice(
            action="seller", kind="set", seller_name="Aayur Supplies",
            seller_address="IIT Bombay", seller_email="a@b.example",
            _store=self.store)
        self.assertIn("seller set: Aayur Supplies", out)
        out = invoice_mod.invoice(action="seller", kind="get",
                                  _store=self.store)
        self.assertIn("Aayur Supplies", out)
        self.assertIn("IIT Bombay", out)

    def test_reads_never_create_the_store(self):
        path = os.path.join(self.tmp, "invoice.sqlite3")
        self.assertFalse(os.path.exists(path))
        out = invoice_mod.invoice(action="status", _store=self.store)
        self.assertIn("no renders yet", out)
        self.assertFalse(os.path.exists(path))
        out = invoice_mod.invoice(action="list", _store=self.store)
        self.assertIn("No invoices rendered yet", out)
        self.assertFalse(os.path.exists(path))

    def test_status_and_events(self):
        self._make_erp_invoice()
        self._render()
        out = invoice_mod.invoice(action="status", _store=self.store)
        self.assertIn("1 render(s)", out)
        self.assertIn("ledger intact", out)
        out = invoice_mod.invoice(action="events", kind="verify",
                                  _store=self.store)
        self.assertIn("ledger intact", out)
        out = invoice_mod.invoice(action="events", kind="list",
                                  _store=self.store)
        self.assertIn("invoice.render", out)

    def test_unknown_action_refused(self):
        out = invoice_mod.invoice(action="explode", _store=self.store)
        self.assertTrue(out.startswith("refused:"), out)


class TestSendEmailAttachments(_InvoiceBase):
    def test_missing_attachment_refuses_before_sending(self):
        from core.google_services_gmail import send_email
        out = send_email(to="a@b.example", subject="x", body="y",
                         attachments=["/tmp/does_not_exist_12345.pdf"])
        self.assertIn("no such file", out)

    def test_too_many_attachments_refused(self):
        from core.google_services_gmail import send_email
        out = send_email(to="a@b.example", subject="x", body="y",
                         attachments=["/tmp/a.pdf"] * 6)
        self.assertIn("at most 5 attachments", out)

    def test_send_invoice_without_gmail_refuses_honestly(self):
        """The send action renders the PDF, then the Gmail send
        fails honestly (no Gmail sign-in in the lab) — the render
        record exists, the send does not, and the answer is a
        refusal, never a false success."""
        self._make_erp_invoice()
        out = invoice_mod.invoice(
            action="send", invoice_id="INV-0001", to="priya@example.com",
            _store=self.store)
        self.assertTrue(out.startswith("refused:"), out)
        # The PDF was rendered (the render record exists) but no
        # send record was written.
        self.assertIsNotNone(self.store.get_row("renders", "RND-0001"))
        self.assertEqual(self.store.count("sends"), 0)


if __name__ == "__main__":
    unittest.main()
