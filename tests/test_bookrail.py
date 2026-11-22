"""R17-1 bookings pack — suite.

Own stdlib booking engine over the tree's own scheduler timezone
shapes + its own SQLite store (the cost_ledger.py precedent):
bookable resources with capacity, timezone, weekly schedule,
buffers and booking window; availability with explain naming
what took each instant; temporary holds; bookings with the rules
frozen at booking time and an idempotency key held by a UNIQUE
constraint; cancel / reschedule / no-show applying the frozen
rules; a hash-chained event ledger; the double-booking guarantee
in SQLite BEFORE INSERT / BEFORE UPDATE triggers. Every
behavioural test drives a temp SQLite file (the handler's
_db_path seam); nothing here touches the production store, the
network, or a real lesson, studio or rental. The public booking
link/page, payment capture and any MCP-server / HTTP / CLI
exposure are NOT built, so nothing here claims them.
"""

import datetime
import json
import pathlib
import re
import sqlite3
import tempfile
import unittest

from core.bookings import _parse_schedule, _schedule_windows, bookings
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
)
from core.funnel.tools_gate_egress import _is_egress_read
from core.funnel.tools_registry import tools as _registry

ROOT = pathlib.Path(__file__).resolve().parent.parent

NAME = "bookings"


def _weekday(days_ahead: int) -> datetime.date:
    """A Mon–Fri date at least `days_ahead` out."""
    day = datetime.date.today() + datetime.timedelta(days=days_ahead)
    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    return day


def _at(day: datetime.date, time: str) -> str:
    return f"{day.isoformat()} {time}"


class BookingsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(pathlib.Path(self.tmp.name) / "b.sqlite3")
        self.addCleanup(self.tmp.cleanup)
        self.day1 = _weekday(2)
        self.day2 = _weekday(3)

    def _resource(self, **over):
        args = {"action": "resource", "resource": "Studio A",
                "capacity": 1, "timezone": "Asia/Kolkata",
                "schedule": "Mon-Fri 09:00-17:00", "buffer_minutes": 15,
                "booking_window_days": 30, "cancel_hours": 24}
        args.update(over)
        return bookings(_db_path=self.db, **args)

    def _book(self, **over):
        args = {"action": "book", "resource": "Studio A",
                "start": _at(self.day1, "10:00"),
                "end": _at(self.day1, "11:00"), "customer": "Ada"}
        args.update(over)
        return bookings(_db_path=self.db, **args)

    def _rows(self, sql, args=()):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def test_registry_counts(self):
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 769, len(decls))  # INTENTIONAL — R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2121, total_params)  # INTENTIONAL — R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 766, grouped)  # INTENTIONAL — R17-1 bookrail pack (2026-11-11): 723 -> 724 — bookings grouped in Your world; R17-2 trivy pack (2026-11-11): 724 -> 725 — security_scan grouped in System & apps; R17-3 go2rtc pack (2026-11-11): 725 -> 726 — ip_camera grouped in Hands & eyes; R17-4 MCSManager pack (2026-11-11): 726 -> 727 — game_server grouped in Hands & eyes; R17-5 komga pack (2026-11-11): 727 -> 728 — ebook_library grouped in Files & media; R17-6 openstays pack (2026-11-11): 728 -> 729 — lodging grouped in Your world; R17-7 qlcplus pack (2026-11-12): 729 -> 730 — stage_lighting grouped in Hands & eyes; R17-8 pvlib pack (2026-11-12): 730 -> 731 — solar_yield grouped in Your world; R17-9 wardrobe pack (2026-11-12): 731 -> 732 — wardrobe grouped in Your world; R18-1 stripe pack (2026-11-12): 732 -> 733 — billing grouped in Your world; R18-3 mcp-context-forge pack (2026-11-12): 733 -> 734 — mcp_registry grouped in Mind; R18-4 a2a pack (2026-11-12): 734 -> 735 — agent_call grouped in Mind; R18-5 dspy pack (2026-11-12): 735 -> 736 — prompt_tune grouped in Mind; R18-6 fonoster pack (2026-11-12): 736 -> 737 — voice_agent grouped in Your world; R18-7 attendize pack (2026-11-12): 737 -> 738 — event_tickets grouped in Everyday skills; R18-8 ta pack (2026-11-12): 738 -> 739 — technical_analysis grouped in Your world; R18-10 timetrace pack (2026-11-12): 739 -> 740 — time_tracker grouped in Everyday skills; R19-1 videolingo pack (2026-11-12): 740 -> 741 — audio_localise grouped in Play & create; R19-2 ortools pack (2026-11-12): 741 -> 742 — optimise grouped in Everyday skills; R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        closed = sum(
            1 for d in decls
            if d.get("parameters", {}).get("additionalProperties") is False)
        self.assertEqual(closed, 768, closed)  # INTENTIONAL — R17-1 bookrail pack (2026-11-11): 724 -> 725 — bookings added (additionalProperties False by the registry default, so the new tool is closed); R17-2 trivy pack (2026-11-11): 725 -> 726 — security_scan added (additionalProperties False by the registry default, so the new tool is closed); R17-3 go2rtc pack (2026-11-11): 726 -> 727 — ip_camera added (additionalProperties False by the registry default, so the new tool is closed); R17-4 MCSManager pack (2026-11-11): 727 -> 728 — game_server added (additionalProperties False by the registry default, so the new tool is closed); R17-5 komga pack (2026-11-11): 728 -> 729 — ebook_library added (additionalProperties False by the registry default, so the new tool is closed); R17-6 openstays pack (2026-11-11): 729 -> 730 — lodging added (additionalProperties False by the registry default, so the new tool is closed); R17-7 qlcplus pack (2026-11-12): 730 -> 731 — stage_lighting added (additionalProperties False by the registry default, so the new tool is closed); R17-8 pvlib pack (2026-11-12): 731 -> 732 — solar_yield added (additionalProperties False by the registry default, so the new tool is closed); R17-9 wardrobe pack (2026-11-12): 732 -> 733 — wardrobe added (additionalProperties False by the registry default, so the new tool is closed); R18-1 stripe pack (2026-11-12): 733 -> 734 — billing added (additionalProperties False by the registry default, so the new tool is closed); R18-3 mcp-context-forge pack (2026-11-12): 734 -> 735 — mcp_registry added (additionalProperties False by the registry default, so the new tool is closed); R18-4 a2a pack (2026-11-12): 735 -> 736 — agent_call added (additionalProperties False by the registry default, so the new tool is closed); R18-5 dspy pack (2026-11-12): 736 -> 737 — prompt_tune added (additionalProperties False by the registry default, so the new tool is closed); R18-6 fonoster pack (2026-11-12): 737 -> 738 — voice_agent added (additionalProperties False by the registry default, so the new tool is closed); R18-7 attendize pack (2026-11-12): 738 -> 739 — event_tickets added (additionalProperties False by the registry default, so the new tool is closed); R18-8 ta pack (2026-11-12): 739 -> 740 — technical_analysis added (additionalProperties False by the registry default, so the new tool is closed); R18-10 timetrace pack (2026-11-12): 740 -> 741 — time_tracker added (additionalProperties False by the registry default, so the new tool is closed); R19-1 videolingo pack (2026-11-12): 741 -> 742 — audio_localise added (additionalProperties False by the registry default, so the new tool is closed); R19-2 ortools pack (2026-11-12): 742 -> 743 — optimise added (additionalProperties False by the registry default, so the new tool is closed); R19-3 aureuserp pack (2026-11-12): 743 -> 744 — erp added (additionalProperties False by the registry default, so the new tool is closed); R19-4 vidbee pack (2026-11-12): 744 -> 745 — media_chat added (additionalProperties False by the registry default, so the new tool is closed); R19-5 invoify pack (2026-11-12): 745 -> 746 — invoice added (additionalProperties False by the registry default, so the new tool is closed); R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        names = {d["name"] for d in decls}
        self.assertIn(NAME, names, NAME)

    def test_audit_totals(self):
        text = (ROOT / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        for needle in ('"total": 769', '"read": 384',  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
                       '"effectful": 385', '"confirm_gated": 169',  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-3 mcp-context-forge pack (2026-11-12): 154 -> 155; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-4 a2a pack (2026-11-12): 155 -> 156; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-5 dspy pack (2026-11-12): 156 -> 157; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-6 fonoster pack (2026-11-12): 157 -> 158; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374/164 -> 375/165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
                       '"already_verified_in_handler": 319',  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 287 -> 288; R18-4 a2a pack (2026-11-12): 288 -> 289; R18-5 dspy pack (2026-11-12): 289 -> 290; R18-6 fonoster pack (2026-11-12): 290 -> 291; R18-7 attendize pack (2026-11-12): 291 -> 292; R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
                       '"productivity": 152'):  # INTENTIONAL — R18-7 attendize pack (2026-11-12): productivity 133 -> 134 (effectful 75 -> 76); R18-8 ta pack (2026-11-12): productivity 134 -> 135 (effectful 76 -> 77); R18-10 timetrace pack (2026-11-12): productivity 135 -> 136 (effectful 77 -> 78); R19-2 ortools pack (2026-11-12): productivity 136 -> 137 (effectful 78 -> 79); R19-3 aureuserp pack (2026-11-12): productivity 137 -> 138 (effectful 79 -> 80); R19-5 invoify pack (2026-11-12): productivity 138 -> 139 (effectful 80 -> 81); R19-6 tolgee pack (2026-11-12): productivity 139 -> 140 (effectful 81 -> 82); R19-7 spoolman pack (2026-11-12): productivity 140 -> 141 (effectful 82 -> 83); R19-8 djangocrm pack (2026-11-12): productivity 141 -> 142 (effectful 83 -> 84); R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 -> 145 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 -> 146 — one new tool run_plan; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
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

    def test_resource_and_availability(self):
        answer = self._resource()
        self.assertIn("Resource 'Studio A'", answer)
        self.assertIn("capacity 1", answer)
        self.assertIn("Asia/Kolkata", answer)
        avail = bookings(action="availability", resource="Studio A",
                         start=_at(self.day1, "09:00"),
                         end=_at(self.day1, "17:00"), _db_path=self.db)
        self.assertIn(f"Free: {self.day1.isoformat()} 09:00", avail)
        self.assertIn("local", avail)

    def test_book_and_re_read_proof(self):
        """SQLite-trigger + re-read: the booking is claimed only
        after the row is found again with the expected id, times
        and confirmed status."""
        self._resource()
        answer = self._book(price=30, idempotency_key="key-1")
        self.assertTrue(answer.startswith("Booked bk_"), answer)
        booking_id = answer.split()[1]
        rows = self._rows(
            "SELECT resource, start_utc, end_utc, status,"
            " idempotency_key FROM bookings WHERE id = ?", (booking_id,))
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0][0], "Studio A")
        self.assertEqual(rows[0][3], "confirmed")
        self.assertEqual(rows[0][4], "key-1")
        self.assertIn("recorded quote", answer)
        self.assertIn("no payment taken", answer)

    def test_overlap_refused_with_explain(self):
        self._resource()
        first = self._book()
        first_id = first.split()[1]
        answer = self._book(start=_at(self.day1, "10:30"),
                            end=_at(self.day1, "11:30"), customer="Bob")
        self.assertIn("not free", answer)
        self.assertIn(first_id, answer, answer)
        self.assertIn("Ada", answer)
        rows = self._rows("SELECT COUNT(*) FROM bookings")
        self.assertEqual(rows[0][0], 1, rows)

    def test_buffer_refused(self):
        self._resource()
        self._book()
        answer = self._book(start=_at(self.day1, "11:05"),
                            end=_at(self.day1, "12:00"), customer="Bob")
        self.assertIn("not free", answer)
        self.assertIn("buffer", answer, answer)
        ok = self._book(start=_at(self.day1, "11:15"),
                        end=_at(self.day1, "12:00"), customer="Bob")
        self.assertTrue(ok.startswith("Booked bk_"), ok)

    def test_capacity_two(self):
        self._resource(resource="Court 1", capacity=2)
        one = bookings(action="book", resource="Court 1",
                       start=_at(self.day1, "10:00"),
                       end=_at(self.day1, "11:00"), customer="A",
                       _db_path=self.db)
        two = bookings(action="book", resource="Court 1",
                       start=_at(self.day1, "10:00"),
                       end=_at(self.day1, "11:00"), customer="B",
                       _db_path=self.db)
        three = bookings(action="book", resource="Court 1",
                         start=_at(self.day1, "10:00"),
                         end=_at(self.day1, "11:00"), customer="C",
                         _db_path=self.db)
        self.assertTrue(one.startswith("Booked bk_"), one)
        self.assertTrue(two.startswith("Booked bk_"), two)
        self.assertIn("not free", three)
        self.assertIn("capacity full", three, three)

    def test_idempotency_key(self):
        self._resource()
        first = self._book(idempotency_key="key-9")
        first_id = first.split()[1]
        again = self._book(idempotency_key="key-9")
        self.assertIn("Already booked under that key", again)
        self.assertIn(first_id, again)
        rows = self._rows("SELECT COUNT(*) FROM bookings")
        self.assertEqual(rows[0][0], 1, rows)

    def test_hold_blocks_and_expires(self):
        self._resource()
        held = bookings(action="hold", resource="Studio A",
                        start=_at(self.day1, "14:00"),
                        end=_at(self.day1, "15:00"), customer="Carol",
                        hold_minutes=30, _db_path=self.db)
        self.assertTrue(held.startswith("Hold hd_"), held)
        blocked = self._book(start=_at(self.day1, "14:30"),
                             end=_at(self.day1, "15:30"), customer="Dan")
        self.assertIn("not free", blocked)
        self.assertIn("hold hd_", blocked, blocked)
        # An expired hold releases itself: backdate its expiry
        # directly in the store, then the same slot books.
        hold_id = held.split()[1]
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "UPDATE holds SET expires_utc = '2000-01-01 00:00:00'"
                " WHERE id = ?", (hold_id,))
            conn.commit()
        finally:
            conn.close()
        freed = self._book(start=_at(self.day1, "14:30"),
                           end=_at(self.day1, "15:30"), customer="Dan")
        self.assertTrue(freed.startswith("Booked bk_"), freed)

    def test_cancel_free_and_late(self):
        # Time-independent: a huge cancel window makes any cancel
        # late; a zero window makes any cancel before the start free.
        self._resource(cancel_hours=10000)
        booked = self._book(start=_at(self.day2, "10:00"),
                            end=_at(self.day2, "11:00"), customer="Late")
        late = bookings(action="cancel",
                        booking_id=booked.split()[1], _db_path=self.db)
        self.assertIn("Late cancellation", late, late)
        self._resource(cancel_hours=0)
        booked2 = self._book(start=_at(self.day2, "12:00"),
                             end=_at(self.day2, "13:00"), customer="Free")
        free = bookings(action="cancel",
                        booking_id=booked2.split()[1], _db_path=self.db)
        self.assertIn("Free cancellation", free, free)

    def test_frozen_rules_govern_cancel(self):
        """The cancel window is the one frozen at booking time,
        not the resource's current policy: booked under a 1000h
        window, the resource then shortened to 0h, and the cancel
        is still late."""
        self._resource(cancel_hours=1000)
        booked = self._book(start=_at(self.day2, "10:00"),
                            end=_at(self.day2, "11:00"), customer="Froz")
        booking_id = booked.split()[1]
        self._resource(cancel_hours=0)
        answer = bookings(action="cancel", booking_id=booking_id,
                          _db_path=self.db)
        self.assertIn("Late cancellation", answer, answer)

    def test_reschedule(self):
        self._resource()
        booked = self._book()
        booking_id = booked.split()[1]
        moved = bookings(action="reschedule", booking_id=booking_id,
                         start=_at(self.day1, "13:00"),
                         end=_at(self.day1, "14:00"), _db_path=self.db)
        self.assertTrue(moved.startswith("Moved"), moved)
        rows = self._rows(
            "SELECT start_utc FROM bookings WHERE id = ?", (booking_id,))
        self.assertEqual(rows[0][0][:10], self.day1.isoformat(), rows)
        taken = self._book(start=_at(self.day1, "10:00"),
                           end=_at(self.day1, "11:00"), customer="Eve")
        self.assertTrue(taken.startswith("Booked bk_"), taken)
        refused = bookings(action="reschedule", booking_id=booking_id,
                           start=_at(self.day1, "10:30"),
                           end=_at(self.day1, "11:30"), _db_path=self.db)
        self.assertIn("not free", refused, refused)

    def test_no_show(self):
        self._resource()
        future = self._book()
        booking_id = future.split()[1]
        early = bookings(action="no-show", booking_id=booking_id,
                         _db_path=self.db)
        self.assertIn("has not started yet", early, early)
        # A booking whose start has passed, placed directly.
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO bookings (id, resource, start_utc,"
                " end_utc, customer, price, status, idempotency_key,"
                " rules_json, created_utc) VALUES ('bk_past',"
                " 'Studio A', '2000-01-01 10:00:00',"
                " '2000-01-01 11:00:00', 'Past', NULL, 'confirmed',"
                " 'past-1', '{}', '2000-01-01 00:00:00')")
            conn.commit()
        finally:
            conn.close()
        marked = bookings(action="no-show", booking_id="bk_past",
                          _db_path=self.db)
        self.assertIn("no-show", marked, marked)

    def test_events_chain_and_tamper(self):
        self._resource()
        self._book()
        events = bookings(action="events", resource="Studio A",
                          _db_path=self.db)
        self.assertIn("Chain intact.", events, events)
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "UPDATE events SET detail = 'tampered' WHERE seq = 2")
            conn.commit()
        finally:
            conn.close()
        broken = bookings(action="events", resource="Studio A",
                          _db_path=self.db)
        self.assertIn("Chain BROKEN at event #2", broken, broken)

    def test_trigger_aborts_direct_sql(self):
        """The guarantee lives in the DB: a direct overlapping
        INSERT aborts in the trigger, not in the handler."""
        self._resource()
        self._book()
        day = self.day1.isoformat()
        conn = sqlite3.connect(self.db)
        try:
            with self.assertRaises(sqlite3.IntegrityError) as caught:
                conn.execute(
                    "INSERT INTO bookings (id, resource, start_utc,"
                    " end_utc, customer, price, status,"
                    " idempotency_key, rules_json, created_utc)"
                    " VALUES ('bk_direct', 'Studio A',"
                    f" '{day} 05:00:00', '{day} 06:00:00',"
                    " 'Mallory', NULL, 'confirmed', 'direct-1', '{}',"
                    " '2000-01-01 00:00:00')")
            self.assertIn("double booking", str(caught.exception))
        finally:
            conn.close()

    def test_dst_day_materialises(self):
        """A local-clock schedule materialised into UTC: the
        spring-forward day has 23 hours, so 00:00–23:59 local
        spans 22h59m of UTC."""
        from zoneinfo import ZoneInfo
        res = {"name": "Rome Room", "capacity": 1,
               "timezone": "Europe/Rome",
               "schedule": "Sun 00:00-23:59", "buffer_minutes": 0,
               "booking_window_days": 30, "cancel_hours": 24}
        tz = ZoneInfo("Europe/Rome")
        start = datetime.datetime(2026, 3, 28, 0, 0,
                                  tzinfo=datetime.timezone.utc)
        end = datetime.datetime(2026, 3, 30, 0, 0,
                                tzinfo=datetime.timezone.utc)
        windows = _schedule_windows(res, start, end, tz)
        self.assertEqual(len(windows), 1, windows)
        span = windows[0][1] - windows[0][0]
        self.assertEqual(span, datetime.timedelta(hours=22, minutes=59),
                         span)

    def test_refusals(self):
        self.assertIn("Give the resource a name",
                      bookings(action="resource", _db_path=self.db))
        self.assertIn("whole number",
                      self._resource(capacity=0))
        self.assertIn("whole number",
                      self._resource(capacity=2.5))
        self.assertIn("Unknown timezone",
                      self._resource(timezone="Nowhere/Nowhere"))
        self.assertIn("Schedule needs weekly windows",
                      self._resource(schedule="Everyday 9-5"))
        self.assertIn("No resource named",
                      bookings(action="availability", resource="Nope",
                               start=_at(self.day1, "09:00"),
                               end=_at(self.day1, "10:00"),
                               _db_path=self.db))
        self._resource()
        self.assertIn("end has to be after the start",
                      self._book(start=_at(self.day1, "11:00"),
                                 end=_at(self.day1, "10:00")))
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        self.assertIn("start in the future",
                      self._book(start=_at(yesterday, "10:00"),
                                 end=_at(yesterday, "11:00")))
        far = _weekday(40)
        self.assertIn("Outside the booking window",
                      self._book(start=_at(far, "10:00"),
                                 end=_at(far, "11:00")))
        self.assertIn("price needs to be a number",
                      self._book(price=-5))
        self.assertIn("Say who the booking is for",
                      self._book(customer=""))
        self.assertIn("No booking",
                      bookings(action="cancel", booking_id="bk_nope",
                               _db_path=self.db))

    def test_no_network_hygiene(self):
        text = (ROOT / "core" / "bookings.py").read_text(encoding="utf-8")
        for bad in (r"^\s*import urllib\b", r"^\s*import requests\b",
                    r"^\s*import socket\b", r"^\s*import httpx\b",
                    r"^\s*from urllib\s+import"):
            self.assertIsNone(re.search(bad, text, re.M), bad)
        for bad in ("django", "flask"):
            self.assertNotIn(bad, text.lower(), bad)
        # The held-back halves are documented, not silently absent.
        for held in ("public booking", "ruling 34", "MCP-server"):
            self.assertIn(held, text, held)


if __name__ == "__main__":
    unittest.main()
