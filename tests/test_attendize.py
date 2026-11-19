"""R18-7 Attendize/Attendize rebuild — local event ticketing over
the existing event-planning pack (ticket types, sale records,
QR check-in).

Every behavioural test drives a temp MemoryStore (the module's
_store seam, shared with event_planning), a temp SQLite ledger
(the _get_ledger seam) and a temp QR dir (the _qr_root seam);
nothing here touches the production store, the production ledger,
the network, or a real event. No payment is taken anywhere — a
sale is a record the owner made, and the money moved outside
VYUK. The hosted ticketing site, payment capture, refunds as
money movement, multi-organiser accounts, public event listings,
messaging attendees and affiliate tracking are NOT built, so
nothing here claims them.
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".pkgroot"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import event_planning as ep
from core import event_ticketing as et
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
    _needs_confirmation,
)
from core.funnel.tools_registry import tools as _registry
from core.memory.memory import MemoryStore

TREE = Path(__file__).resolve().parent.parent

PLAN = "Spring wedding"


class AttendizeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(
            str(Path(self.tmp.name) / "facts.db"))
        self._store_patch_ep = mock.patch.object(
            ep, "_store", lambda: self.store)
        self._store_patch_et = mock.patch.object(
            et, "_store", lambda: self.store)
        self._ledger_patch = mock.patch.object(
            et, "_get_ledger",
            lambda: et._Ledger(str(Path(self.tmp.name) / "ledger.db")))
        self._qr_patch = mock.patch.object(
            et, "_qr_root", lambda: Path(self.tmp.name) / "qr")
        self._store_patch_ep.start()
        self._store_patch_et.start()
        self._ledger_patch.start()
        self._qr_patch.start()
        self.addCleanup(self._store_patch_ep.stop)
        self.addCleanup(self._store_patch_et.stop)
        self.addCleanup(self._ledger_patch.stop)
        self.addCleanup(self._qr_patch.stop)
        self.addCleanup(self.tmp.cleanup)
        created = ep.event_plan("create", PLAN, "2027-02-14",
                                120, "standard", 1500000)
        self.assertIn("Created the plan", created)
        self.assertIn("Added ticket type #1", et.event_tickets(
            action="type", mode="add", plan=PLAN,
            name="General Admission", price=25, capacity=100,
            description="Standing room"))
        self.assertIn("Added ticket type #2", et.event_tickets(
            action="type", mode="add", plan=PLAN, name="VIP",
            price=100, capacity=3))

    def _sale(self, buyer="Asha", qty=2, type_id=2, method="cash",
              ref="C-101"):
        return et.event_tickets(action="sale", mode="add", plan=PLAN,
                                type_id=type_id, buyer=buyer, qty=qty,
                                pay_method=method, pay_ref=ref)


class TestRegistryCounts(AttendizeTestCase):
    def test_registry_counts(self):
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 758, len(decls))  # INTENTIONAL — R18-7 attendize pack (2026-11-12): 739 -> 740 — event_tickets added; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2079, total_params)  # INTENTIONAL — R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 755, grouped)  # INTENTIONAL — R18-7 attendize pack (2026-11-12): 737 -> 738 — event_tickets grouped in Your world; R18-8 ta pack (2026-11-12): 738 -> 739 — technical_analysis grouped in Your world; R18-10 timetrace pack (2026-11-12): 739 -> 740 — time_tracker grouped in Everyday skills; R19-1 videolingo pack (2026-11-12): 740 -> 741 — audio_localise grouped in Play & create; R19-2 ortools pack (2026-11-12): 741 -> 742 — optimise grouped in Everyday skills; R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing
        closed = sum(
            1 for d in decls
            if d.get("parameters", {}).get("additionalProperties") is False)
        self.assertEqual(closed, 757, closed)  # INTENTIONAL — R18-7 attendize pack (2026-11-12): 738 -> 739 — event_tickets added (additionalProperties False by the registry default, so the new tool is closed); R18-8 ta pack (2026-11-12): 739 -> 740 — technical_analysis added (additionalProperties False by the registry default, so the new tool is closed); R18-10 timetrace pack (2026-11-12): 740 -> 741 — time_tracker added (additionalProperties False by the registry default, so the new tool is closed); R19-1 videolingo pack (2026-11-12): 741 -> 742 — audio_localise added (additionalProperties False by the registry default, so the new tool is closed); R19-2 ortools pack (2026-11-12): 742 -> 743 — optimise added (additionalProperties False by the registry default, so the new tool is closed); R19-3 aureuserp pack (2026-11-12): 743 -> 744 — erp added (additionalProperties False by the registry default, so the new tool is closed); R19-4 vidbee pack (2026-11-12): 744 -> 745 — media_chat added (additionalProperties False by the registry default, so the new tool is closed); R19-5 invoify pack (2026-11-12): 745 -> 746 — invoice added (additionalProperties False by the registry default, so the new tool is closed); R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing
        names = {d["name"] for d in decls}
        self.assertIn("event_tickets", names)
        props = next(d for d in decls if d["name"] == "event_tickets")
        self.assertEqual(len(props["parameters"]["properties"]), 15)
        self.assertEqual(props["parameters"]["required"],
                         ["action", "plan"])

    def test_audit_totals(self):
        text = (TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        for needle in ('"total": 758', '"read": 383',  # INTENTIONAL — R18-7 attendize pack (2026-11-12): audit needles 739/357/291 -> 740/358/292; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing
                       '"effectful": 375', '"confirm_gated": 166',  # INTENTIONAL — R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374/164 -> 375/165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling
                       '"already_verified_in_handler": 309',  # INTENTIONAL — R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan
                       '"productivity": 146'):  # INTENTIONAL — R18-8 ta pack (2026-11-12): productivity 134 -> 135 (effectful 76 -> 77); R18-10 timetrace pack (2026-11-12): productivity 135 -> 136 (effectful 77 -> 78); R19-2 ortools pack (2026-11-12): productivity 136 -> 137 (effectful 78 -> 79); R19-3 aureuserp pack (2026-11-12): productivity 137 -> 138 (effectful 79 -> 80); R19-5 invoify pack (2026-11-12): productivity 138 -> 139 (effectful 80 -> 81); R19-6 tolgee pack (2026-11-12): productivity 139 -> 140 (effectful 81 -> 82); R19-7 spoolman pack (2026-11-12): productivity 140 -> 141 (effectful 82 -> 83); R19-8 djangocrm pack (2026-11-12): productivity 141 -> 142 (effectful 83 -> 84); R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 -> 145 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 -> 146 — one new tool run_plan
            self.assertIn(needle, text, needle)
        totals = json.loads(re.search(
            r"<!-- AUDIT-TOTALS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(totals["total"], 758)  # INTENTIONAL — R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertEqual(totals["read"], 383)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 382 -> 383 — one new read tool get_morning_briefing
        self.assertEqual(totals["effectful"], 375)  # INTENTIONAL — R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374 -> 375 — one new tool run_plan
        self.assertEqual(totals["confirm_gated"], 166)  # INTENTIONAL — R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling
        self.assertEqual(totals["already_verified_in_handler"], 309)  # INTENTIONAL — R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan
        counts = json.loads(re.search(
            r"<!-- AUDIT-COUNTS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(counts["productivity"], 146)  # INTENTIONAL — R18-8 ta pack (2026-11-12): productivity 134 -> 135 (effectful 76 -> 77); R18-10 timetrace pack (2026-11-12): productivity 135 -> 136 (effectful 77 -> 78); R19-2 ortools pack (2026-11-12): productivity 136 -> 137 (effectful 78 -> 79); R19-3 aureuserp pack (2026-11-12): productivity 137 -> 138 (effectful 79 -> 80); R19-5 invoify pack (2026-11-12): productivity 138 -> 139 (effectful 80 -> 81); R19-6 tolgee pack (2026-11-12): productivity 139 -> 140 (effectful 81 -> 82); R19-7 spoolman pack (2026-11-12): productivity 140 -> 141 (effectful 82 -> 83); R19-8 djangocrm pack (2026-11-12): productivity 141 -> 142 (effectful 83 -> 84); R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 -> 145 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 -> 146 — one new tool run_plan
        row = next(line for line in text.splitlines()
                   if line.startswith("| productivity |"))
        self.assertIn("| 146 | 88 |", row)  # INTENTIONAL — R18-8 ta pack (2026-11-12): productivity 134 -> 135 (effectful 76 -> 77); R18-10 timetrace pack (2026-11-12): productivity 135 -> 136 (effectful 77 -> 78); R19-2 ortools pack (2026-11-12): productivity 136 -> 137 (effectful 78 -> 79); R19-3 aureuserp pack (2026-11-12): productivity 137 -> 138 (effectful 79 -> 80); R19-5 invoify pack (2026-11-12): productivity 138 -> 139 (effectful 80 -> 81); R19-6 tolgee pack (2026-11-12): productivity 139 -> 140 (effectful 81 -> 82); R19-7 spoolman pack (2026-11-12): productivity 140 -> 141 (effectful 82 -> 83); R19-8 djangocrm pack (2026-11-12): productivity 141 -> 142 (effectful 83 -> 84); R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 | 86 -> 145 | 87 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 | 87 -> 146 | 88 — one new tool run_plan


class TestTicketTypes(AttendizeTestCase):
    def test_type_list_and_get(self):
        listed = et.event_tickets(action="type", mode="list", plan=PLAN)
        self.assertIn("General Admission", listed)
        self.assertIn("VIP", listed)
        got = et.event_tickets(action="type", mode="get", plan=PLAN,
                               type_id=2)
        self.assertIn("VIP", got)
        self.assertIn("100", got)
        self.assertIn("3", got)

    def test_type_add_duplicate_is_refused(self):
        self.assertIn("already a ticket type", et.event_tickets(
            action="type", mode="add", plan=PLAN,
            name="general admission", price=30))

    def test_type_add_bad_price_and_capacity_are_refused(self):
        self.assertIn("zero or more", et.event_tickets(
            action="type", mode="add", plan=PLAN, name="Bad",
            price=-5))
        self.assertIn("zero or more", et.event_tickets(
            action="type", mode="add", plan=PLAN, name="Bad",
            price=10, capacity=-1))

    def test_type_add_needs_a_name(self):
        self.assertIn("needs the ticket type's name", et.event_tickets(
            action="type", mode="add", plan=PLAN, name="  ",
            price=10))

    def test_type_remove_with_sales_is_refused(self):
        self._sale()
        self.assertIn("already sold", et.event_tickets(
            action="type", mode="remove", plan=PLAN, type_id=2))

    def test_type_remove_cleanly_when_never_sold(self):
        self.assertIn("Removed ticket type #1", et.event_tickets(
            action="type", mode="remove", plan=PLAN, type_id=1))
        listed = et.event_tickets(action="type", mode="list", plan=PLAN)
        self.assertNotIn("General Admission", listed)
        self.assertIn("VIP", listed)

    def test_type_get_unknown_is_refused(self):
        self.assertIn("No ticket type #99", et.event_tickets(
            action="type", mode="get", plan=PLAN, type_id=99))

    def test_plan_must_exist(self):
        self.assertIn("No plan called", et.event_tickets(
            action="type", mode="add", plan="No such plan",
            name="GA", price=10))


class TestSales(AttendizeTestCase):
    def test_sale_add_records_codes_total_and_qr(self):
        r = self._sale()
        self.assertIn("Recorded sale #1", r)
        self.assertIn("SPR-0001", r)
        self.assertIn("SPR-0002", r)
        self.assertIn("200", r)
        self.assertIn("a record, not a payment", r)
        qr1 = Path(self.tmp.name) / "qr" / "spring-wedding" / "qr_SPR-0001.png"
        qr2 = Path(self.tmp.name) / "qr" / "spring-wedding" / "qr_SPR-0002.png"
        self.assertTrue(qr1.exists())
        self.assertTrue(qr2.exists())
        self.assertEqual(qr1.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(qr2.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_sale_add_oversell_is_refused_with_numbers(self):
        self._sale()
        r = et.event_tickets(action="sale", mode="add", plan=PLAN,
                             type_id=2, buyer="Ben", qty=2,
                             pay_method="cash")
        self.assertIn("won't fit", r)
        self.assertIn("3", r)
        self.assertIn("2", r)

    def test_sale_add_unknown_type_and_bad_qty_are_refused(self):
        self.assertIn("No ticket type #99", et.event_tickets(
            action="sale", mode="add", plan=PLAN, type_id=99,
            buyer="Ben", qty=1))
        self.assertIn("between 1 and 100", et.event_tickets(
            action="sale", mode="add", plan=PLAN, type_id=2,
            buyer="Ben", qty=0))
        self.assertIn("between 1 and 100", et.event_tickets(
            action="sale", mode="add", plan=PLAN, type_id=2,
            buyer="Ben", qty=101))

    def test_sale_add_needs_a_buyer(self):
        self.assertIn("needs the buyer's name", et.event_tickets(
            action="sale", mode="add", plan=PLAN, type_id=2,
            buyer="  ", qty=1))

    def test_sale_list_and_get(self):
        self._sale()
        listed = et.event_tickets(action="sale", mode="list", plan=PLAN)
        self.assertIn("sale #1", listed)
        self.assertIn("Asha", listed)
        self.assertIn("200", listed)
        got = et.event_tickets(action="sale", mode="get", plan=PLAN,
                               sale_id=1)
        self.assertIn("SPR-0001", got)
        self.assertIn("SPR-0002", got)
        self.assertIn("qr_SPR-0001.png", got)

    def test_sale_void_and_void_twice(self):
        self._sale()
        r = et.event_tickets(action="sale", mode="remove", plan=PLAN,
                             sale_id=1)
        self.assertIn("Voided sale #1", r)
        self.assertIn("no longer check in", r)
        self.assertIn("already void", et.event_tickets(
            action="sale", mode="remove", plan=PLAN, sale_id=1))

    def test_sale_get_unknown_is_refused(self):
        self.assertIn("No sale #99", et.event_tickets(
            action="sale", mode="get", plan=PLAN, sale_id=99))


class TestCheckin(AttendizeTestCase):
    def test_checkin_success_duplicate_unknown_and_voided(self):
        self._sale()
        r = et.event_tickets(action="checkin", mode="checkin",
                             plan=PLAN, code="spr-0001")
        self.assertIn("Checked in ticket 'SPR-0001'", r)
        self.assertIn("Asha", r)
        self.assertIn("already checked in", et.event_tickets(
            action="checkin", mode="checkin", plan=PLAN,
            code="SPR-0001"))
        self.assertIn("No ticket with the code", et.event_tickets(
            action="checkin", mode="checkin", plan=PLAN,
            code="SPR-9999"))
        et.event_tickets(action="sale", mode="remove", plan=PLAN,
                         sale_id=1)
        self.assertIn("voided", et.event_tickets(
            action="checkin", mode="checkin", plan=PLAN,
            code="SPR-0002"))

    def test_checkin_list_and_status(self):
        self._sale()
        et.event_tickets(action="checkin", mode="checkin", plan=PLAN,
                         code="SPR-0001")
        cl = et.event_tickets(action="checkin", mode="list", plan=PLAN)
        self.assertIn("1 in, 1 still to arrive", cl)
        self.assertIn("SPR-0001", cl)
        self.assertIn("SPR-0002", cl)
        st = et.event_tickets(action="status", plan=PLAN)
        self.assertIn("2 ticket(s) issued, 1 checked in, "
                      "1 still to arrive", st)
        self.assertIn("200", st)
        self.assertIn("not a payment total", st)

    def test_checkin_needs_a_code(self):
        self._sale()
        self.assertIn("needs the ticket's code", et.event_tickets(
            action="checkin", mode="checkin", plan=PLAN, code="  "))

    def test_status_on_a_plan_with_no_tickets(self):
        ep.event_plan("create", "Empty gala", "2027-03-01",
                      50, "modest", 10000)
        self.assertIn("No tickets on 'Empty gala' yet",
                      et.event_tickets(action="status",
                                       plan="Empty gala"))

    def test_unknown_action_and_mode_are_refused(self):
        self.assertIn("actions are", et.event_tickets(
            action="nonsense", plan=PLAN))
        self.assertIn("modes are", et.event_tickets(
            action="type", mode="nonsense", plan=PLAN))
        self.assertIn("modes are", et.event_tickets(
            action="checkin", mode="nonsense", plan=PLAN))


class TestLedger(AttendizeTestCase):
    def test_events_verify_and_record_everything(self):
        self._sale()
        et.event_tickets(action="checkin", mode="checkin", plan=PLAN,
                         code="SPR-0001")
        ev = et.event_tickets(action="events", plan=PLAN)
        self.assertIn("ledger verified", ev)
        self.assertIn("type_add", ev)
        self.assertIn("sale_add", ev)
        self.assertIn("checkin", ev)

    def test_events_empty_when_nothing_happened(self):
        # A plan with no types, no sales and no check-ins has no
        # events at all. (setUp already created two types on the
        # main plan, so this uses a fresh one.)
        ep.event_plan("create", "Empty gala", "2027-03-01",
                      50, "modest", 10000)
        ev = et.event_tickets(action="events", plan="Empty gala")
        self.assertIn("No ticket events yet", ev)

    def test_ledger_tamper_is_caught(self):
        led = et._Ledger(str(Path(self.tmp.name) / "tamper.db"))
        led.append_event("type_add", {"plan": "X", "type_id": 1})
        led.append_event("sale_add", {"plan": "X", "sale_id": 1})
        ok, _ = led.verify_chain()
        self.assertTrue(ok)
        import sqlite3
        conn = sqlite3.connect(str(Path(self.tmp.name) / "tamper.db"))
        conn.execute("UPDATE events SET hash='deadbeef' WHERE seq=2")
        conn.commit()
        conn.close()
        led2 = et._Ledger(str(Path(self.tmp.name) / "tamper.db"))
        ok, reason = led2.verify_chain()
        self.assertFalse(ok)
        self.assertIn("does not recompute", reason)
        led.close()
        led2.close()


class TestGates(AttendizeTestCase):
    def test_free_by_design_no_confirmation(self):
        self.assertNotIn("event_tickets", _CONFIRM_GATE_TOOLS)
        for action in ("type", "sale", "checkin", "status", "events"):
            self.assertFalse(_needs_confirmation(
                "event_tickets", {"action": action}), action)
        self.assertNotIn("event_tickets", _READ_ONLY_TOOLS)
        self.assertEqual(_FREE_BY_DESIGN.get("event_tickets"),
                         "app-state")

    def test_classification_snapshot(self):
        snapshot = json.loads(
            (TREE / "tests" / "_classification_snapshot.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(snapshot["event_tickets"],
                         {"confirm": False, "free": "app-state",
                          "read": False})

    def test_catalogue_has_event_tickets_in_everyday_skills(self):
        # With the rest of the event-planning family (event_plan,
        # event_schedule, ...), not in "Your world".
        self.assertIn("event_tickets",
                      GROUP_MEMBERS["Everyday skills"])

    def test_licence_marker_is_proposed_or_parent_ruled(self):
        # Hardened (the R18-1/R18-5 trap): the parent's ruling edit
        # flips the marker after this run, so either state passes.
        text = (TREE / "core" / "event_ticketing.py").read_text(
            encoding="utf-8")
        self.assertTrue("PROPOSED — parent rules" in text
                        or "Parent-ruled" in text)


if __name__ == "__main__":
    unittest.main()
