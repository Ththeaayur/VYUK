"""R16-10 warehouse-stock pack — suite.

Own stdlib warehouse over the tree's own memory-facts + admin
shapes (stock locations with quantities, inbound receive,
outbound ship, moves between locations, per-location occupancy,
one stock row per (item, location) with a movement ledger).
Every behavioural test drives a temp MemoryStore (the helpers'
_store seam); nothing here touches the production store, the
network, or a real warehouse. The Django web app, the hosted
multi-user shape, staff roles, the customer portal and any
storage billing are NOT built, so nothing here claims them.
"""

import json
import pathlib
import re
import tempfile
import unittest
from unittest import mock

from core import skills_admin_helpers as helpers
from core import skills_admin_lend_assets as lend
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
)
from core.funnel.tools_gate_egress import _is_egress_read
from core.funnel.tools_registry import tools as _registry
from core.memory.memory import MemoryStore

ROOT = pathlib.Path(__file__).resolve().parent.parent

NAME = "warehouse_stock"


class WarehouseStockTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(
            str(pathlib.Path(self.tmp.name) / "facts.db"))
        self._store_patch = mock.patch.object(
            helpers, "_store", lambda: self.store)
        self._store_patch.start()
        self.addCleanup(self._store_patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def _receive(self, **over):
        args = {"action": "receive", "item": "Screws",
                "location": "Aisle 1", "quantity": 10,
                "capacity": 100, "detail": "M3 x 12"}
        args.update(over)
        return lend.warehouse_stock(**args)

    def test_registry_counts(self):
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 764, len(decls))  # INTENTIONAL — R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2102, total_params)  # INTENTIONAL — R16-10 GreaterWMS pack (2026-11-11): 1650 -> 1658 — warehouse_stock adds 8 properties (action, item, location, from_location, to_location, quantity, capacity, detail); R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 761, grouped)  # INTENTIONAL — R16-10 GreaterWMS pack (2026-11-11): 722 -> 723 — warehouse_stock grouped in Your world; R17-1 bookrail pack (2026-11-11): 723 -> 724 — bookings grouped in Your world; R17-2 trivy pack (2026-11-11): 724 -> 725 — security_scan grouped in System & apps; R17-3 go2rtc pack (2026-11-11): 725 -> 726 — ip_camera grouped in Hands & eyes; R17-4 MCSManager pack (2026-11-11): 726 -> 727 — game_server grouped in Hands & eyes; R17-5 komga pack (2026-11-11): 727 -> 728 — ebook_library grouped in Files & media; R17-6 openstays pack (2026-11-11): 728 -> 729 — lodging grouped in Your world; R17-7 qlcplus pack (2026-11-12): 729 -> 730 — stage_lighting grouped in Hands & eyes; R17-8 pvlib pack (2026-11-12): 730 -> 731 — solar_yield grouped in Your world; R17-9 wardrobe pack (2026-11-12): 731 -> 732 — wardrobe grouped in Your world; R18-1 stripe pack (2026-11-12): 732 -> 733 — billing grouped in Your world; R18-3 mcp-context-forge pack (2026-11-12): 733 -> 734 — mcp_registry grouped in Mind; R18-4 a2a pack (2026-11-12): 734 -> 735 — agent_call grouped in Mind; R18-5 dspy pack (2026-11-12): 735 -> 736 — prompt_tune grouped in Mind; R18-6 fonoster pack (2026-11-12): 736 -> 737 — voice_agent grouped in Your world; R18-7 attendize pack (2026-11-12): 737 -> 738 — event_tickets grouped in Everyday skills; R18-8 ta pack (2026-11-12): 738 -> 739 — technical_analysis grouped in Your world; R18-10 timetrace pack (2026-11-12): 739 -> 740 — time_tracker grouped in Everyday skills; R19-1 videolingo pack (2026-11-12): 740 -> 741 — audio_localise grouped in Play & create; R19-2 ortools pack (2026-11-12): 741 -> 742 — optimise grouped in Everyday skills; R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        closed = sum(
            1 for d in decls
            if d.get("parameters", {}).get("additionalProperties") is False)
        self.assertEqual(closed, 763, closed)  # INTENTIONAL — R16-10 GreaterWMS pack (2026-11-11): 723 -> 724 — warehouse_stock added (additionalProperties False by the registry default, so the new tool is closed); R17-1 bookrail pack (2026-11-11): 724 -> 725 — bookings added (additionalProperties False by the registry default, so the new tool is closed); R17-2 trivy pack (2026-11-11): 725 -> 726 — security_scan added (additionalProperties False by the registry default, so the new tool is closed); R17-3 go2rtc pack (2026-11-11): 726 -> 727 — ip_camera added (additionalProperties False by the registry default, so the new tool is closed); R17-4 MCSManager pack (2026-11-11): 727 -> 728 — game_server added (additionalProperties False by the registry default, so the new tool is closed); R17-5 komga pack (2026-11-11): 728 -> 729 — ebook_library added (additionalProperties False by the registry default, so the new tool is closed); R17-6 openstays pack (2026-11-11): 729 -> 730 — lodging added (additionalProperties False by the registry default, so the new tool is closed); R17-7 qlcplus pack (2026-11-12): 730 -> 731 — stage_lighting added (additionalProperties False by the registry default, so the new tool is closed); R17-8 pvlib pack (2026-11-12): 731 -> 732 — solar_yield added (additionalProperties False by the registry default, so the new tool is closed); R17-9 wardrobe pack (2026-11-12): 732 -> 733 — wardrobe added (additionalProperties False by the registry default, so the new tool is closed); R18-1 stripe pack (2026-11-12): 733 -> 734 — billing added (additionalProperties False by the registry default, so the new tool is closed); R18-3 mcp-context-forge pack (2026-11-12): 734 -> 735 — mcp_registry added (additionalProperties False by the registry default, so the new tool is closed); R18-4 a2a pack (2026-11-12): 735 -> 736 — agent_call added (additionalProperties False by the registry default, so the new tool is closed); R18-5 dspy pack (2026-11-12): 736 -> 737 — prompt_tune added (additionalProperties False by the registry default, so the new tool is closed); R18-6 fonoster pack (2026-11-12): 737 -> 738 — voice_agent added (additionalProperties False by the registry default, so the new tool is closed); R18-7 attendize pack (2026-11-12): 738 -> 739 — event_tickets added (additionalProperties False by the registry default, so the new tool is closed); R18-8 ta pack (2026-11-12): 739 -> 740 — technical_analysis added (additionalProperties False by the registry default, so the new tool is closed); R18-10 timetrace pack (2026-11-12): 740 -> 741 — time_tracker added (additionalProperties False by the registry default, so the new tool is closed); R19-1 videolingo pack (2026-11-12): 741 -> 742 — audio_localise added (additionalProperties False by the registry default, so the new tool is closed); R19-2 ortools pack (2026-11-12): 742 -> 743 — optimise added (additionalProperties False by the registry default, so the new tool is closed); R19-3 aureuserp pack (2026-11-12): 743 -> 744 — erp added (additionalProperties False by the registry default, so the new tool is closed); R19-4 vidbee pack (2026-11-12): 744 -> 745 — media_chat added (additionalProperties False by the registry default, so the new tool is closed); R19-5 invoify pack (2026-11-12): 745 -> 746 — invoice added (additionalProperties False by the registry default, so the new tool is closed); R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        names = {d["name"] for d in decls}
        self.assertIn(NAME, names, NAME)

    def test_audit_totals(self):
        text = (ROOT / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        for needle in ('"total": 764', '"read": 384',  # INTENTIONAL — R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
                       '"effectful": 380', '"confirm_gated": 169',  # INTENTIONAL — R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-3 mcp-context-forge pack (2026-11-12): 154 -> 155; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-4 a2a pack (2026-11-12): 155 -> 156; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-5 dspy pack (2026-11-12): 156 -> 157; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-6 fonoster pack (2026-11-12): 157 -> 158; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374/164 -> 375/165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
                       '"already_verified_in_handler": 314',  # INTENTIONAL — R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 287 -> 288; R18-4 a2a pack (2026-11-12): 288 -> 289; R18-5 dspy pack (2026-11-12): 289 -> 290; R18-6 fonoster pack (2026-11-12): 290 -> 291; R18-7 attendize pack (2026-11-12): 291 -> 292; R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
                       '"productivity": 148'):  # INTENTIONAL — R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R17-6 openstays pack (2026-11-11): productivity 130 -> 131 — lodging grouped in Your world; R18-7 attendize pack (2026-11-12): productivity 133 -> 134 (effectful 75 -> 76); R18-8 ta pack (2026-11-12): productivity 134 -> 135 (effectful 76 -> 77); R18-10 timetrace pack (2026-11-12): productivity 135 -> 136 (effectful 77 -> 78); R19-2 ortools pack (2026-11-12): productivity 136 -> 137 (effectful 78 -> 79); R19-3 aureuserp pack (2026-11-12): productivity 137 -> 138 (effectful 79 -> 80); R19-5 invoify pack (2026-11-12): productivity 138 -> 139 (effectful 80 -> 81); R19-6 tolgee pack (2026-11-12): productivity 139 -> 140 (effectful 81 -> 82); R19-7 spoolman pack (2026-11-12): productivity 140 -> 141 (effectful 82 -> 83); R19-8 djangocrm pack (2026-11-12): productivity 141 -> 142 (effectful 83 -> 84); R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 -> 145 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 -> 146 — one new tool run_plan; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
            self.assertIn(needle, text, needle)

    def test_gates(self):
        self.assertEqual(_FREE_BY_DESIGN.get(NAME), "app-state", NAME)
        self.assertNotIn(NAME, _CONFIRM_GATE_TOOLS, NAME)
        self.assertNotIn(NAME, _READ_ONLY_TOOLS, NAME)
        self.assertFalse(_is_egress_read(NAME))
        snap = json.loads(
            (ROOT / "tests" / "_classification_snapshot.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(
            snap[NAME],
            {"confirm": False, "free": "app-state", "read": False},
            NAME)

    def test_receive_and_list(self):
        answer = self._receive()
        self.assertIn("Received 10 × 'Screws' at Aisle 1", answer)
        self.assertIn("(10 on hand now)", answer)
        self.assertIn("Capacity set to 100 units", answer)
        listed = lend.warehouse_stock("list")
        self.assertIn("Warehouse stock (1 row):", listed)
        self.assertIn("Screws — Aisle 1 — 10 on hand", listed)
        self.assertIn("M3 x 12", listed)
        self.assertIn("Total: 10 units across 1 row in 1 location.",
                      listed)
        self.assertIn("Recent movements:", listed)
        self.assertIn("received 10 × Screws at Aisle 1", listed)

    def test_receive_re_read_proof(self):
        """A1-store: the receive is claimed only after the stock
        row is found again with the expected quantity."""
        self._receive()
        entries, broken = lend._load_warehouse_stock()
        self.assertEqual(broken, 0)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["item"], "Screws")
        self.assertEqual(entries[0]["location"], "Aisle 1")
        self.assertEqual(entries[0]["quantity"], 10)
        self.assertEqual(entries[0]["_key"], "aisle 1|screws")
        moves, _ = lend._load_warehouse_moves()
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["kind"], "receive")

    def test_receive_same_location_accumulates(self):
        self._receive()
        answer = self._receive(quantity=5)
        self.assertIn("(15 on hand now)", answer)
        listed = lend.warehouse_stock("list")
        self.assertIn("Warehouse stock (1 row):", listed)
        self.assertIn("15 on hand", listed)

    def test_ship(self):
        self._receive()
        answer = lend.warehouse_stock("ship", "Screws", "Aisle 1",
                                      quantity=4)
        self.assertIn("Shipped 4 × 'Screws' from Aisle 1", answer)
        self.assertIn("(6 left)", answer)
        listed = lend.warehouse_stock("list")
        self.assertIn("6 on hand", listed)

    def test_ship_shortfall_refused(self):
        self._receive()
        answer = lend.warehouse_stock("ship", "Screws", "Aisle 1",
                                      quantity=11)
        self.assertIn("Only 10 × 'Screws' on hand at Aisle 1", answer)
        self.assertIn("nothing was shipped", answer)
        listed = lend.warehouse_stock("list")
        self.assertIn("10 on hand", listed)

    def test_ship_missing_refused(self):
        answer = lend.warehouse_stock("ship", "Bolts", "Aisle 1",
                                      quantity=1)
        self.assertIn("No 'Bolts' stored at Aisle 1", answer)
        self.assertIn("nothing was shipped", answer)

    def test_move(self):
        self._receive()
        answer = lend.warehouse_stock(
            "move", "Screws", from_location="Aisle 1",
            to_location="Aisle 2", quantity=3)
        self.assertIn("Moved 3 × 'Screws' from Aisle 1 to Aisle 2",
                      answer)
        self.assertIn("(7 left at Aisle 1, 3 at Aisle 2)", answer)
        listed = lend.warehouse_stock("list")
        self.assertIn("Screws — Aisle 1 — 7 on hand", listed)
        self.assertIn("Screws — Aisle 2 — 3 on hand", listed)
        self.assertIn("moved 3 × Screws from Aisle 1 to Aisle 2",
                      listed)

    def test_move_shortfall_refused(self):
        self._receive()
        answer = lend.warehouse_stock(
            "move", "Screws", from_location="Aisle 1",
            to_location="Aisle 2", quantity=11)
        self.assertIn("Only 10 × 'Screws' on hand at Aisle 1", answer)
        self.assertIn("nothing was moved", answer)

    def test_move_same_location_refused(self):
        self._receive()
        answer = lend.warehouse_stock(
            "move", "Screws", from_location="Aisle 1",
            to_location="aisle 1", quantity=1)
        self.assertIn("same location", answer)
        self.assertIn("nothing was moved", answer)

    def test_occupancy(self):
        self._receive()
        self._receive(item="Bolts", quantity=40)
        answer = lend.warehouse_stock("occupancy")
        self.assertIn("Warehouse occupancy:", answer)
        self.assertIn("Aisle 1 — 50 of 100 units (50% full)", answer)
        self.assertIn("Total: 50 units across 1 location.", answer)
        self._receive(item="Nuts", location="Aisle 2", quantity=5,
                      capacity=None)
        answer = lend.warehouse_stock("occupancy")
        self.assertIn("Aisle 2 — 5 units (capacity not set)", answer)

    def test_find(self):
        self._receive()
        self._receive(item="Bolts", quantity=2)
        by_item = lend.warehouse_stock("find", item="screw")
        self.assertIn("Found 1:", by_item)
        self.assertIn("Screws", by_item)
        by_loc = lend.warehouse_stock("find", location="aisle 1")
        self.assertIn("Found 2:", by_loc)
        none = lend.warehouse_stock("find", item="sofa")
        self.assertIn("Nothing in the warehouse matches", none)
        empty = lend.warehouse_stock("find")
        self.assertIn("needs something to look for", empty)

    def test_bad_quantity_refused(self):
        for bad in (0, -1, 2.5, "ten"):
            answer = self._receive(quantity=bad)
            self.assertIn("whole number, 1 or more", answer)
            self.assertIn("Nothing was moved", answer)
        self.assertIn("warehouse is empty",
                      lend.warehouse_stock("list"))

    def test_bad_capacity_refused(self):
        answer = self._receive(capacity=0)
        self.assertIn("Capacity needs to be a whole number", answer)
        self.assertIn("Nothing was received", answer)
        self.assertIn("warehouse is empty",
                      lend.warehouse_stock("list"))

    def test_unknown_action(self):
        self.assertIn("actions are",
                      lend.warehouse_stock("bogus", item="Screws"))

    def test_broken_fact_tolerated(self):
        self._receive()
        self.store.save_fact("warehouse_stock", "broken", "not-json{{{")
        listed = lend.warehouse_stock("list")
        self.assertIn("Screws", listed)
        self.assertIn("couldn't be read", listed)

    def test_no_network_imports(self):
        text = (ROOT / "core" / "skills_admin_lend_assets.py").read_text(
            encoding="utf-8")
        for bad in (r"^\s*import urllib\b", r"^\s*import requests\b",
                    r"^\s*import socket\b", r"^\s*import httpx\b",
                    r"^\s*from urllib\s+import",
                    r"^\s*from requests\s+import"):
            self.assertIsNone(re.search(bad, text, re.M), bad)
        self.assertIn("never copies or opens the file", text)


if __name__ == "__main__":
    unittest.main()
