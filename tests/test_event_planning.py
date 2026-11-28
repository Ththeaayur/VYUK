"""R16-8 event-planning pack — suite.

Own stdlib wedding / event planning dashboard over the tree's own
money + task + scheduler shapes (plan / guests / vendors / budget
estimator / milestones / seating / run-of-show). Every behavioural
test drives a temp MemoryStore (the module's _store seam) and a
temp ProactiveScheduler (the _get_scheduler seam); nothing here
touches the production store, the production scheduler, the
network, or a real event. The public wedding website, the RSVP
link flow and the honeymoon fund are NOT built (hosting/share +
guest-response + ruling 34 — needs owner rulings), so nothing
here claims them.
"""

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from core import event_planning as ep
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
)
from core.funnel.tools_gate_egress import (
    _LOCAL_ONLY_READS,
    _is_egress_read,
)
from core.funnel.tools_registry import tools as _registry
from core.memory.memory import MemoryStore
from core.scheduler import ProactiveScheduler

ROOT = pathlib.Path(__file__).resolve().parent.parent

NAMES = (
    "event_plan",
    "event_guest",
    "event_vendor",
    "event_budget",
    "event_milestone",
    "event_schedule",
)
WRITING = (
    "event_plan",
    "event_guest",
    "event_vendor",
    "event_milestone",
    "event_schedule",
)


class EventPlanningTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(
            str(pathlib.Path(self.tmp.name) / "facts.db"))
        self.scheduler = ProactiveScheduler(
            store_path=pathlib.Path(self.tmp.name) / "scheduler.json")
        self._store_patch = mock.patch.object(
            ep, "_store", lambda: self.store)
        self._sched_patch = mock.patch.object(
            ep, "_get_scheduler", lambda: self.scheduler)
        self._store_patch.start()
        self._sched_patch.start()
        self.addCleanup(self._store_patch.stop)
        self.addCleanup(self._sched_patch.stop)
        self.addCleanup(self.tmp.cleanup)
        ep.event_plan("create", "Spring wedding", "2027-02-14",
                      120, "standard", 1500000)

    def test_registry_counts(self):
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 772, len(decls))  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2126, total_params)  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): 1607 -> 1640 — event_plan 7 + event_guest 7 + event_vendor 5 + event_budget 5 + event_milestone 5 + event_schedule 4 properties; R16-9 attic pack (2026-11-11): 1640 -> 1650 — home_inventory adds 10 properties (action, item, box, room, condition, value, purchased, photo, detail, quantity); R16-10 GreaterWMS pack (2026-11-11): 1650 -> 1658 — warehouse_stock adds 8 properties (action, item, location, from_location, to_location, quantity, capacity, detail); R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 772, grouped)  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): 715 -> 721 — six tools grouped in Everyday skills; R16-9 attic pack (2026-11-11): 721 -> 722 — home_inventory grouped in Everyday skills; R16-10 GreaterWMS pack (2026-11-11): 722 -> 723 — warehouse_stock grouped in Your world; R17-1 bookrail pack (2026-11-11): 723 -> 724 — bookings grouped in Your world; R17-2 trivy pack (2026-11-11): 724 -> 725 — security_scan grouped in System & apps; R17-3 go2rtc pack (2026-11-11): 725 -> 726 — ip_camera grouped in Hands & eyes; R17-4 MCSManager pack (2026-11-11): 726 -> 727 — game_server grouped in Hands & eyes; R17-5 komga pack (2026-11-11): 727 -> 728 — ebook_library grouped in Files & media; R17-6 openstays pack (2026-11-11): 728 -> 729 — lodging grouped in Your world; R17-7 qlcplus pack (2026-11-12): 729 -> 730 — stage_lighting grouped in Hands & eyes; R17-8 pvlib pack (2026-11-12): 730 -> 731 — solar_yield grouped in Your world; R17-9 wardrobe pack (2026-11-12): 731 -> 732 — wardrobe grouped in Your world; R18-1 stripe pack (2026-11-12): 732 -> 733 — billing grouped in Your world; R18-3 mcp-context-forge pack (2026-11-12): 733 -> 734 — mcp_registry grouped in Mind; R18-4 a2a pack (2026-11-12): 734 -> 735 — agent_call grouped in Mind; R18-5 dspy pack (2026-11-12): 735 -> 736 — prompt_tune grouped in Mind; R18-6 fonoster pack (2026-11-12): 736 -> 737 — voice_agent grouped in Your world; R18-7 attendize pack (2026-11-12): 737 -> 738 — event_tickets grouped in Everyday skills; R18-8 ta pack (2026-11-12): 738 -> 739 — technical_analysis grouped in Your world; R18-10 timetrace pack (2026-11-12): 739 -> 740 — time_tracker grouped in Everyday skills; R19-1 videolingo pack (2026-11-12): 740 -> 741 — audio_localise grouped in Play & create; R19-2 ortools pack (2026-11-12): 741 -> 742 — optimise grouped in Everyday skills; R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170; UI Phase 2 (2026-11-25): grouped 769 -> 772 — click_anywhere + 2 plugin tools join the catalogue
        closed = sum(
            1 for d in decls
            if d.get("parameters", {}).get("additionalProperties") is False)
        self.assertEqual(closed, 771, closed)  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): 716 -> 722 — six tools added (additionalProperties False by the registry default, so the new tools are closed); R16-9 attic pack (2026-11-11): 722 -> 723 — home_inventory added (additionalProperties False by the registry default, so the new tool is closed); R16-10 GreaterWMS pack (2026-11-11): INTENTIONAL — closed count 723 -> 724: warehouse_stock added (additionalProperties False by the registry default, so the new tool is closed); R17-1 bookrail pack (2026-11-11): 724 -> 725 — bookings added (additionalProperties False by the registry default, so the new tool is closed); R17-2 trivy pack (2026-11-11): 725 -> 726 — security_scan added (additionalProperties False by the registry default, so the new tool is closed); R17-3 go2rtc pack (2026-11-11): 726 -> 727 — ip_camera added (additionalProperties False by the registry default, so the new tool is closed); R17-4 MCSManager pack (2026-11-11): 727 -> 728 — game_server added (additionalProperties False by the registry default, so the new tool is closed); R17-5 komga pack (2026-11-11): 728 -> 729 — ebook_library added (additionalProperties False by the registry default, so the new tool is closed); R17-6 openstays pack (2026-11-11): 729 -> 730 — lodging added (additionalProperties False by the registry default, so the new tool is closed); R17-7 qlcplus pack (2026-11-12): 730 -> 731 — stage_lighting added (additionalProperties False by the registry default, so the new tool is closed); R17-8 pvlib pack (2026-11-12): 731 -> 732 — solar_yield added (additionalProperties False by the registry default, so the new tool is closed); R17-9 wardrobe pack (2026-11-12): 732 -> 733 — wardrobe added (additionalProperties False by the registry default, so the new tool is closed); R18-1 stripe pack (2026-11-12): 733 -> 734 — billing added (additionalProperties False by the registry default, so the new tool is closed); R18-3 mcp-context-forge pack (2026-11-12): 734 -> 735 — mcp_registry added (additionalProperties False by the registry default, so the new tool is closed); R18-4 a2a pack (2026-11-12): 735 -> 736 — agent_call added (additionalProperties False by the registry default, so the new tool is closed); R18-5 dspy pack (2026-11-12): 736 -> 737 — prompt_tune added (additionalProperties False by the registry default, so the new tool is closed); R18-6 fonoster pack (2026-11-12): 737 -> 738 — voice_agent added (additionalProperties False by the registry default, so the new tool is closed); R18-7 attendize pack (2026-11-12): 738 -> 739 — event_tickets added (additionalProperties False by the registry default, so the new tool is closed); R18-8 ta pack (2026-11-12): 739 -> 740 — technical_analysis added (additionalProperties False by the registry default, so the new tool is closed); R18-10 timetrace pack (2026-11-12): 740 -> 741 — time_tracker added (additionalProperties False by the registry default, so the new tool is closed); R19-1 videolingo pack (2026-11-12): 741 -> 742 — audio_localise added (additionalProperties False by the registry default, so the new tool is closed); R19-2 ortools pack (2026-11-12): 742 -> 743 — optimise added (additionalProperties False by the registry default, so the new tool is closed); R19-3 aureuserp pack (2026-11-12): 743 -> 744 — erp added (additionalProperties False by the registry default, so the new tool is closed); R19-4 vidbee pack (2026-11-12): 744 -> 745 — media_chat added (additionalProperties False by the registry default, so the new tool is closed); R19-5 invoify pack (2026-11-12): 745 -> 746 — invoice added (additionalProperties False by the registry default, so the new tool is closed); R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        names = {d["name"] for d in decls}
        for name in NAMES:
            self.assertIn(name, names, name)

    def test_audit_totals(self):
        text = (ROOT / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        for needle in ('"total": 772', '"read": 386',  # INTENTIONAL — R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
                       '"effectful": 386', '"confirm_gated": 170',  # INTENTIONAL — R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-3 mcp-context-forge pack (2026-11-12): 154 -> 155; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-4 a2a pack (2026-11-12): 155 -> 156; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-5 dspy pack (2026-11-12): 156 -> 157; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-6 fonoster pack (2026-11-12): 157 -> 158; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374/164 -> 375/165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
                       '"already_verified_in_handler": 320',  # INTENTIONAL — R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 287 -> 288; R18-4 a2a pack (2026-11-12): 288 -> 289; R18-5 dspy pack (2026-11-12): 289 -> 290; R18-6 fonoster pack (2026-11-12): 290 -> 291; R18-7 attendize pack (2026-11-12): 291 -> 292; R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
                       '"productivity": 152'):  # INTENTIONAL — R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R17-6 openstays pack (2026-11-11): productivity 130 -> 131 — lodging grouped in Your world; R18-7 attendize pack (2026-11-12): productivity 133 -> 134 (effectful 75 -> 76); R18-8 ta pack (2026-11-12): productivity 134 -> 135 (effectful 76 -> 77); R18-10 timetrace pack (2026-11-12): productivity 135 -> 136 (effectful 77 -> 78); R19-2 ortools pack (2026-11-12): productivity 136 -> 137 (effectful 78 -> 79); R19-3 aureuserp pack (2026-11-12): productivity 137 -> 138 (effectful 79 -> 80); R19-5 invoify pack (2026-11-12): productivity 138 -> 139 (effectful 80 -> 81); R19-6 tolgee pack (2026-11-12): productivity 139 -> 140 (effectful 81 -> 82); R19-7 spoolman pack (2026-11-12): productivity 140 -> 141 (effectful 82 -> 83); R19-8 djangocrm pack (2026-11-12): productivity 141 -> 142 (effectful 83 -> 84); R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 -> 145 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 -> 146 — one new tool run_plan; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
            self.assertIn(needle, text, needle)

    def test_gates(self):
        self.assertIn("event_budget", _READ_ONLY_TOOLS)
        self.assertIn("event_budget", _LOCAL_ONLY_READS)
        self.assertFalse(_is_egress_read("event_budget"))
        for name in WRITING:
            self.assertEqual(_FREE_BY_DESIGN.get(name), "app-state", name)
            self.assertNotIn(name, _CONFIRM_GATE_TOOLS, name)
            self.assertNotIn(name, _READ_ONLY_TOOLS, name)
        snap = json.loads(
            (ROOT / "tests" / "_classification_snapshot.json")
            .read_text(encoding="utf-8"))
        for name in WRITING:
            self.assertEqual(
                snap[name],
                {"confirm": False, "free": "app-state", "read": False},
                name)
        self.assertEqual(
            snap["event_budget"],
            {"confirm": False, "free": None, "read": True})

    def test_create_and_summary(self):
        answer = ep.event_plan("summary", "Spring wedding")
        self.assertIn("Spring wedding", answer)
        self.assertIn("2027-02-14", answer)
        self.assertIn("1,500,000.00", answer)
        listed = ep.event_plan("summary")
        self.assertIn("Spring wedding", listed)
        self.assertIn("Event plans (1):", listed)
        dup = ep.event_plan("create", "Spring wedding")
        self.assertIn("already a plan", dup)
        self.assertIn("No plan called 'No such plan'",
                      ep.event_plan("summary", "No such plan"))
        self.assertIn("isn't a date",
                      ep.event_plan("create", "Bad", "32-13-99"))
        self.assertIn("isn't a tier",
                      ep.event_plan("create", "Bad", "", 0, "royal"))

    def test_guests(self):
        self.assertIn(
            "Added 'Asha'",
            ep.event_guest("add", "Spring wedding", "Asha",
                           2, "veg", "3", "yes"))
        self.assertIn(
            "party of 1",
            ep.event_guest("add", "Spring wedding", "Solo"))
        listed = ep.event_guest("list", "Spring wedding")
        self.assertIn("Asha — party of 2, meal: veg, table 3, RSVP: yes",
                      listed)
        self.assertIn("Solo — party of 1", listed)
        self.assertIn(
            "Moved 'Solo' to table '5'",
            ep.event_guest("set_table", "Spring wedding", "Solo",
                           table="5"))
        self.assertIn("already a guest",
                      ep.event_guest("add", "Spring wedding", "Asha"))
        self.assertIn("isn't an RSVP state",
                      ep.event_guest("add", "Spring wedding", "Bad",
                                     rsvp="perhaps"))
        self.assertIn("needs to be 1 or more",
                      ep.event_guest("add", "Spring wedding", "Zero", 0))
        self.assertIn("is not a guest",
                      ep.event_guest("set_table", "Spring wedding",
                                     "Ghost", table="1"))
        plans, _ = ep._load_plans()
        names = [g["name"] for g in
                 plans["spring wedding"]["guests"]]
        self.assertEqual(names, ["Asha", "Solo"])

    def test_vendors(self):
        ep.event_vendor("add", "Spring wedding", "catering",
                        "Spice House", 400000)
        ep.event_vendor("add", "Spring wedding", "catering",
                        "Green Leaf", 320000)
        compared = ep.event_vendor("compare", "Spring wedding",
                                   "catering")
        self.assertIn("Cheapest: Green Leaf at 320,000.00", compared)
        self.assertIn("Dearest: Spice House at 400,000.00", compared)
        self.assertIn("spread 80,000.00", compared)
        self.assertIn("average 360,000.00", compared)
        listed = ep.event_vendor("list", "Spring wedding")
        self.assertIn("catering:", listed)
        self.assertIn("Quoted total: 720,000.00", listed)
        self.assertIn("already a 'catering' vendor",
                      ep.event_vendor("add", "Spring wedding",
                                      "catering", "Green Leaf", 1))
        self.assertIn("needs to be a number more than zero",
                      ep.event_vendor("add", "Spring wedding",
                                      "flowers", "Rose Co", 0))
        self.assertIn("No 'flowers' vendors",
                      ep.event_vendor("compare", "Spring wedding",
                                      "flowers"))

    def test_budget(self):
        ep.event_vendor("add", "Spring wedding", "catering",
                        "Green Leaf", 320000)
        estimate = ep.event_budget("estimate", "Spring wedding")
        self.assertIn("Guests: 120 x 2,000.00", estimate)
        self.assertIn("Estimated total: 560,000.00", estimate)
        self.assertIn("940,000.00 left", estimate)
        self.assertIn("rough planning defaults", estimate)
        self.assertIn(
            "Guests: 50 x 2,000.00",
            ep.event_budget("estimate", "Spring wedding", guests=50))
        self.assertIn(
            "Guests: 120 x 5,000.00",
            ep.event_budget("estimate", "Spring wedding",
                            per_guest=5000))
        over = ep.event_budget("estimate", "Spring wedding",
                               tier="luxury")
        self.assertIn("Guests: 120 x 12,000.00", over)
        self.assertIn("260,000.00 OVER budget", over)
        self.assertIn(
            "Guests: 120 x 2,000.00",
            ep.event_budget("estimate", "Spring wedding",
                            tier="bogus"))
        summary = ep.event_budget("summary", "Spring wedding")
        self.assertIn("Total budget: 1,500,000.00", summary)
        self.assertIn("catering: 320,000.00", summary)
        self.assertIn("No plan called 'No such plan'",
                      ep.event_budget("estimate", "No such plan"))

    def test_milestones(self):
        self.assertIn(
            "Added milestone 'Book the venue'",
            ep.event_milestone("add", "Spring wedding",
                               "Book the venue", "2026-12-01"))
        listed = ep.event_milestone("list", "Spring wedding")
        self.assertIn("[ ] Book the venue — due 2026-12-01", listed)
        self.assertIn(
            "Marked 'Book the venue' done",
            ep.event_milestone("complete", "Spring wedding",
                               "Book the venue"))
        self.assertIn("already done",
                      ep.event_milestone("complete", "Spring wedding",
                                         "Book the venue"))
        self.assertIn("isn't a date",
                      ep.event_milestone("add", "Spring wedding",
                                         "Bad", "32-13-99"))
        self.assertIn("already a milestone",
                      ep.event_milestone("add", "Spring wedding",
                                         "Book the venue"))

    def test_milestone_reminder(self):
        answer = ep.event_milestone(
            "add", "Spring wedding", "Send invitations",
            "2099-06-01", remind=True)
        self.assertIn("Reminder set", answer)
        reminders = self.scheduler.get_all_reminders()
        self.assertTrue(
            any("Send invitations" in str(r) for r in reminders),
            reminders)
        past = ep.event_milestone(
            "add", "Spring wedding", "Old task",
            "2020-01-01", remind=True)
        self.assertIn("was not set", past)
        self.assertIn("Old task",
                      ep.event_milestone("list", "Spring wedding"))

    def test_schedule(self):
        ep.event_schedule("add", "Spring wedding", "19:00", "Dinner")
        ep.event_schedule("add", "Spring wedding", "18:00",
                          "First dance")
        listed = ep.event_schedule("list", "Spring wedding")
        self.assertLess(listed.index("18:00"), listed.index("19:00"))
        self.assertIn("18:00 — First dance", listed)
        self.assertIn("needs a time of day",
                      ep.event_schedule("add", "Spring wedding",
                                        "25:00", "Late"))
        self.assertIn("needs the item's title",
                      ep.event_schedule("add", "Spring wedding",
                                        "18:00", ""))

    def test_notes(self):
        self.assertIn(
            "Added the note",
            ep.event_plan("add_note", "Spring wedding",
                          note="Ask about fairy lights"))
        self.assertIn(
            "Ask about fairy lights",
            ep.event_plan("summary", "Spring wedding"))

    def test_broken_fact_tolerated(self):
        self.store.save_fact("event_plans", "broken", "not-json{{{")
        listed = ep.event_plan("summary")
        self.assertIn("Spring wedding", listed)
        self.assertIn("couldn't be read", listed)

    def test_unknown_actions(self):
        self.assertIn("actions are",
                      ep.event_plan("bogus", "Spring wedding"))
        self.assertIn("actions are",
                      ep.event_guest("bogus", "Spring wedding"))
        self.assertIn("actions are",
                      ep.event_vendor("bogus", "Spring wedding"))
        self.assertIn("actions are",
                      ep.event_budget("bogus", "Spring wedding"))
        self.assertIn("actions are",
                      ep.event_milestone("bogus", "Spring wedding"))
        self.assertIn("actions are",
                      ep.event_schedule("bogus", "Spring wedding"))

    def test_no_network_imports(self):
        import re

        text = (ROOT / "core" / "event_planning.py").read_text(
            encoding="utf-8")
        for bad in (r"^\s*import urllib\b", r"^\s*import requests\b",
                    r"^\s*import socket\b", r"^\s*import httpx\b",
                    r"^\s*from urllib\s+import",
                    r"^\s*from requests\s+import"):
            self.assertIsNone(re.search(bad, text, re.M), bad)
        self.assertIsNotNone(re.search(r"Nothing is\s+adopted", text))


if __name__ == "__main__":
    unittest.main()
