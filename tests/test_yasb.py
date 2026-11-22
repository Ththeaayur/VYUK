"""R8-6 yasb — the status strip as VYUK's own code (PORT, not adopt:
YASB is a full standalone Windows status-bar app; what is ported is
the widget-catalogue pattern only — a small set of live widgets in a
bar — built from VYUK's own pieces: the Pulse sampler seam and VYUK's
own Qt surfaces. No YASB code, no YAML/CSS config surface, no new
dependency, requirements.txt untouched). Pins: the tool registration
(1 tool, 1 property, closed), the funnel gates, the sink seam (fake
sink round trips, no-sink honesty, bad actions), the strip widget
under a fake sampler (the 8 numbers behind a tap, honest "—", timer
only while visible, docked 32 px row — one dot + one word; UI
rethink Build 3, 2026-11-16), and the boundary (no media / no
workspaces / no pomodoro / no per-app polling — the tree has no
now-playing source, no virtual-desktop concept, and well.py is
sensing-free).

Widget tests need PySide6, which system python3 in this lab does not
have — they skip there (the doctorui precedent) and run on the Qt
interpreter. Core / gate / registry tests run on both.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import sys
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import status_strip
from core.status_strip import (
    get_status_strip_sink,
    set_status_strip_sink,
    status_strip as strip_tool,
)
from core.funnel.tools_gate import _FREE_BY_DESIGN
from core.funnel.tools_registry import tools as _registry

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _register_packages():
    """Mirror test_wiring_triage_v600._register_packages: make
    voice_assistant.* importable from this folder whatever it is
    called. gui.status_strip now imports
    voice_assistant.core.config_spec (GAPS-11), so the widget
    tests need the shim — the established pattern for gui
    modules, which already import core via voice_assistant.*."""
    if "voice_assistant" not in sys.modules:
        pkg = types.ModuleType("voice_assistant")
        pkg.__path__ = [str(ROOT)]
        sys.modules["voice_assistant"] = pkg
    if "voice_assistant.core" not in sys.modules:
        core = types.ModuleType("voice_assistant.core")
        core.__path__ = [str(ROOT / "core")]
        sys.modules["voice_assistant.core"] = core


_register_packages()


def _qt_available() -> bool:
    try:
        import PySide6  # noqa: F401 — availability probe only
    except ImportError:
        return False
    return True


def _fake_sample(**over):
    sample = {
        "cpu": 23.0, "mem": 61.0, "disk": 44.0, "net_bps": 1_500_000.0,
        "battery_pct": 82.0, "battery_plugged": True,
        "uptime_s": 3600.0, "procs": 123.0,
    }
    sample.update(over)
    return sample


class _FakeSink:
    """A stand-in window: applies show/hide/status against a flag."""

    def __init__(self):
        self.visible = False
        self.calls = []

    def __call__(self, action: str) -> dict:
        self.calls.append(action)
        if action == "show":
            self.visible = True
        elif action == "hide":
            self.visible = False
        return {
            "ok": True,
            "visible": self.visible,
            "text": "CPU 23% · MEM 61% · DISK 44% · NET 1.5 MB/s · BAT 82% plugged"
            if self.visible else "",
            "error": None,
        }


class TestRegistration(unittest.TestCase):
    def test_registry_counts(self):
        self.assertEqual(len(_registry.get_declarations()), 769)  # INTENTIONAL — 684 -> 687: wormhole_offer, wormhole_receive, wormhole_status (R8-5 magic-wormhole port, 2026-11-10); R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        names = {d["name"] for d in _registry.get_declarations()}
        self.assertIn("status_strip", names)

    def test_tool_shape_closed_one_property(self):
        decl = next(
            d for d in _registry.get_declarations() if d["name"] == "status_strip"
        )
        self.assertFalse(decl["parameters"].get("additionalProperties", True))
        self.assertEqual(
            list(decl["parameters"]["properties"].keys()), ["action"]
        )
        closed = [
            d for d in _registry.get_declarations()
            if d.get("parameters", {}).get("additionalProperties") is False
        ]
        self.assertEqual(len(closed), 768)  # INTENTIONAL — 686 -> 687: status_strip (R8-6 yasb port, 2026-11-10); R8-7 basic-pitch port (2026-11-10): INTENTIONAL — closed count 687 -> 689: audio_to_midi + audio_to_midi_status added (additionalProperties False by the registry default, so the new tools are closed); R8-8 mftool port (2026-11-10): INTENTIONAL — closed count 689 -> 691: fund_returns, mutual_fund_nav added (additionalProperties False by the registry default, so the new tools are closed); R8-9 pypresence adoption (2026-11-10): INTENTIONAL — closed count 691 -> 693: discord_presence + set_discord_client_id added (additionalProperties False by the registry default, so the new tools are closed); R9-2 rustic adoption (2026-11-10): INTENTIONAL — closed count 693 -> 698: init_backup_repo + backup_files + list_backup_snapshots + restore_backup + backup_status added (additionalProperties False by the registry default, so the new tools are closed); R9-3 whoosh port (2026-11-10): INTENTIONAL — closed count 698 -> 701: index_files, search_indexed_files, disk_index_status added (additionalProperties False by the registry default, so the new tools are closed); R9-4 mutagen port (2026-11-10): INTENTIONAL — closed count 701 -> 703: sync_folders_two_way, two_way_sync_status added (additionalProperties False by the registry default, so the new tools are closed); R9-8 elodie port (2026-11-10): 703 -> 705 — organise_photos, preview_photo_organisation added (additionalProperties False by the registry default, so the new tools are closed); R10-1 chrome-devtools-mcp port (2026-11-10): INTENTIONAL — closed count 705 -> 708: browser_console, browser_network, browser_performance added (additionalProperties False by the registry default, so the new tools are closed); R10-3 browser-history port (2026-11-10): INTENTIONAL — closed count 708 -> 710: browser_history, browser_bookmarks added (additionalProperties False by the registry default, so the new tools are closed); R10-4 python-adblock port (2026-11-10): INTENTIONAL — closed count 710 -> 712: check_url_blocked, filter_list_status added (additionalProperties False by the registry default, so the new tools are closed); R10-5 python-readability port (2026-11-10): INTENTIONAL — closed count 712 -> 713: extract_article added (additionalProperties False by the registry default, so the new tool is closed); R11-1 ollama port (2026-11-10): INTENTIONAL — closed count 713 -> 716: ollama_status (no parameters), ollama_pull + ollama_delete added (additionalProperties False by the registry default, so the new tools are closed); R16-8 event-planning pack (2026-11-11): INTENTIONAL — closed count 716 -> 722: six tools added (additionalProperties False by the registry default, so the new tools are closed); R16-9 attic pack (2026-11-11): INTENTIONAL — closed count 722 -> 723: home_inventory added (additionalProperties False by the registry default, so the new tool is closed); R16-10 GreaterWMS pack (2026-11-11): INTENTIONAL — closed count 723 -> 724: warehouse_stock added (additionalProperties False by the registry default, so the new tool is closed); R17-1 bookrail pack (2026-11-11): 724 -> 725 — bookings added (additionalProperties False by the registry default, so the new tool is closed); R17-2 trivy pack (2026-11-11): 725 -> 726 — security_scan added (additionalProperties False by the registry default, so the new tool is closed); R17-3 go2rtc pack (2026-11-11): 726 -> 727 — ip_camera added (additionalProperties False by the registry default, so the new tool is closed); R17-4 MCSManager pack (2026-11-11): 727 -> 728 — game_server added (additionalProperties False by the registry default, so the new tool is closed); R17-5 komga pack (2026-11-11): 728 -> 729 — ebook_library added (additionalProperties False by the registry default, so the new tool is closed); R17-6 openstays pack (2026-11-11): 729 -> 730 — lodging added (additionalProperties False by the registry default, so the new tool is closed); R17-7 qlcplus pack (2026-11-12): 730 -> 731 — stage_lighting added (additionalProperties False by the registry default, so the new tool is closed); R17-8 pvlib pack (2026-11-12): 731 -> 732 — solar_yield added (additionalProperties False by the registry default, so the new tool is closed); R17-9 wardrobe pack (2026-11-12): 732 -> 733 — wardrobe added (additionalProperties False by the registry default, so the new tool is closed); R18-1 stripe pack (2026-11-12): 733 -> 734 — billing added (additionalProperties False by the registry default, so the new tool is closed); R18-3 mcp-context-forge pack (2026-11-12): 734 -> 735 — mcp_registry added (additionalProperties False by the registry default, so the new tool is closed); R18-4 a2a pack (2026-11-12): 735 -> 736 — agent_call added (additionalProperties False by the registry default, so the new tool is closed); R18-5 dspy pack (2026-11-12): 736 -> 737 — prompt_tune added (additionalProperties False by the registry default, so the new tool is closed); R18-6 fonoster pack (2026-11-12): 737 -> 738 — voice_agent added (additionalProperties False by the registry default, so the new tool is closed); R18-7 attendize pack (2026-11-12): 738 -> 739 — event_tickets added (additionalProperties False by the registry default, so the new tool is closed); R18-8 ta pack (2026-11-12): 739 -> 740 — technical_analysis added (additionalProperties False by the registry default, so the new tool is closed); R18-10 timetrace pack (2026-11-12): 740 -> 741 — time_tracker added (additionalProperties False by the registry default, so the new tool is closed); R19-1 videolingo pack (2026-11-12): 741 -> 742 — audio_localise added (additionalProperties False by the registry default, so the new tool is closed); R19-2 ortools pack (2026-11-12): 742 -> 743 — optimise added (additionalProperties False by the registry default, so the new tool is closed); R19-3 aureuserp pack (2026-11-12): 743 -> 744 — erp added (additionalProperties False by the registry default, so the new tool is closed); R19-4 vidbee pack (2026-11-12): 744 -> 745 — media_chat added (additionalProperties False by the registry default, so the new tool is closed); R19-5 invoify pack (2026-11-12): 745 -> 746 — invoice added (additionalProperties False by the registry default, so the new tool is closed); R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        total_props = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in _registry.get_declarations()
        )
        self.assertEqual(total_props, 2121)  # INTENTIONAL — 1544 -> 1545: status_strip adds 1 property (action) (R8-6 yasb port, 2026-11-10); R8-7 basic-pitch port (2026-11-10): 1545 -> 1551 — audio_to_midi adds 6 properties (audio_path, output, onset_threshold, frame_threshold, min_note_length_ms, tempo_bpm); audio_to_midi_status adds 0; R8-8 mftool port (2026-11-10): 1551 -> 1557 — fund_returns adds 5 properties (start_value, end_value, years, cashflows, currency), mutual_fund_nav adds 1 property (scheme); R8-9 pypresence adoption (2026-11-10): 1557 -> 1563 — discord_presence adds 5 properties (action, details, state, name, client_id); set_discord_client_id adds 1 property (client_id); R9-2 rustic adoption (2026-11-10): 1563 -> 1572 — init_backup_repo adds 1 property (repo_path); backup_files adds 3 (sources, repo_path, label); list_backup_snapshots adds 1 (repo_path); restore_backup adds 3 (snapshot, destination, repo_path); backup_status adds 1 (repo_path); R9-3 whoosh port (2026-11-10): 1572 -> 1576 — index_files adds 2 properties (folders, rebuild), search_indexed_files adds 2 properties (query, limit), disk_index_status adds 0; R9-4 mutagen port (2026-11-10); R9-8 elodie port (2026-11-10): 1580 -> 1585 — organise_photos adds 3 properties (source, destination, dry_run); preview_photo_organisation adds 2 properties (source, destination); R10-1 chrome-devtools-mcp port (2026-11-10): 1585 -> 1591 — browser_console adds 2 properties (seconds, target), browser_network adds 3 (seconds, target, max_entries), browser_performance adds 1 (target); R10-3 browser-history port (2026-11-10): 1591 -> 1598 — browser_history adds 4 properties (profile_dir, browser, days, limit), browser_bookmarks adds 3 (profile_dir, browser, limit); R10-4 python-adblock port (2026-11-10): 1598 -> 1603 — check_url_blocked adds 4 properties (url, list_file, source_domain, resource_type), filter_list_status adds 1 (list_file); R10-5 python-readability port (2026-11-10): 1603 -> 1605 — extract_article adds 2 properties (url, html); R11-1 ollama port (2026-11-10): 1605 -> 1607 — ollama_pull + ollama_delete add 1 property each (model); ollama_status adds 0; R16-8 event-planning pack (2026-11-11): 1607 -> 1640 — event_plan 7 + event_guest 7 + event_vendor 5 + event_budget 5 + event_milestone 5 + event_schedule 4 properties; R16-9 attic pack (2026-11-11): 1640 -> 1650 — home_inventory adds 10 properties (action, item, box, room, condition, value, purchased, photo, detail, quantity); R16-10 GreaterWMS pack (2026-11-11): 1650 -> 1658 — warehouse_stock adds 8 properties (action, item, location, from_location, to_location, quantity, capacity, detail); R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (Group E, 2026-11-22): 2079 -> 2095 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_gate_free_app_state(self):
        self.assertEqual(_FREE_BY_DESIGN.get("status_strip"), "app-state")

    def test_grouped_system_and_apps(self):
        from core.catalogue_snapshot import GROUP_MEMBERS

        self.assertIn("status_strip", GROUP_MEMBERS.get("System & apps", ()))

    def test_toolname(self):
        from gui.window_widgets_toolnames import _TOOL_FRIENDLY_NAMES

        self.assertEqual(_TOOL_FRIENDLY_NAMES.get("status_strip"), "Status strip")

    def test_audit_totals(self):
        text = (ROOT / "TOOLS_LOOPHOLES_AUDIT.md").read_text()
        totals = json.loads(
            text.split("AUDIT-TOTALS: ", 1)[1].split(" -->", 1)[0]
        )
        self.assertEqual(totals["total"], 769)  # INTENTIONAL — R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertEqual(totals["read"], 384)  # INTENTIONAL — R8-7 basic-pitch port (2026-11-10): 361 -> 362 — audio_to_midi_status is read-only; R8-8 mftool port (2026-11-10): 362 -> 364 — fund_returns, mutual_fund_nav are read-only; R9-2 rustic adoption (2026-11-10): 364 -> 366 — list_backup_snapshots + backup_status are read-only; R9-3 whoosh port (2026-11-10): 366 -> 368 — search_indexed_files + disk_index_status are read-only; R9-4 mutagen port (2026-11-10): 368 -> 369 — two_way_sync_status is read-only (see §2c); R9-8 elodie port (2026-11-10): 369 -> 370 — preview_photo_organisation is read-only; R10-1 chrome-devtools-mcp port (2026-11-10): 370 -> 373 — browser_console, browser_network, browser_performance are read-only; R10-3 browser-history port (2026-11-10): 373 -> 375 — browser_history, browser_bookmarks are read-only; R10-4 python-adblock port (2026-11-10): 375 -> 377 — check_url_blocked, filter_list_status are read-only; R10-5 python-readability port (2026-11-10): 377 -> 378 — extract_article is read-only; R11-1 ollama port (2026-11-10): 378 -> 379 — ollama_status is read-only; R16-8 event-planning pack (2026-11-11): 379 -> 380 — event_budget is read-only; R17-2 trivy pack (2026-11-11): 380 -> 381 — security_scan is read-only; R17-8 pvlib pack (2026-11-12): 381 -> 382 — solar_yield is read-only; round-3 fix Q6 (2026-11-19): 382 -> 383 — one new read tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169
        self.assertEqual(totals["effectful"], 385)  # INTENTIONAL — R8-7 basic-pitch port (2026-11-10): 327 -> 328 — audio_to_midi added (effectful); R8-9 pypresence adoption (2026-11-10): 328 -> 330 — discord_presence + set_discord_client_id added (effectful); R9-2 rustic adoption (2026-11-10): 330 -> 333 — init_backup_repo + backup_files + restore_backup added (effectful); R9-3 whoosh port (2026-11-10): 333 -> 334 — index_files added (effectful); R9-4 mutagen port (2026-11-10): 334 -> 335 — sync_folders_two_way added (effectful); R9-8 elodie port (2026-11-10): 335 -> 336 — organise_photos added (effectful); R11-1 ollama port (2026-11-10): 336 -> 338 — ollama_pull + ollama_delete are effectful; R16-8 event-planning pack (2026-11-11): 338 -> 343 — event_plan + event_guest + event_vendor + event_milestone + event_schedule are effectful; R16-9 attic pack (2026-11-11): 343 -> 344 — home_inventory is effectful; R16-10 GreaterWMS pack (2026-11-11): 344 -> 345 — warehouse_stock is effectful; R17-1 bookrail pack (2026-11-11): 345 -> 346 — bookings added (effectful); R17-3 go2rtc pack (2026-11-11): 346 -> 347 — ip_camera added (effectful); R17-4 MCSManager pack (2026-11-11): 347 -> 348 — game_server added (effectful); R17-5 komga pack (2026-11-11): 348 -> 349 — ebook_library added (effectful); R17-6 openstays pack (2026-11-11): 349 -> 350 — lodging added (effectful); R17-7 qlcplus pack (2026-11-12): 350 -> 351 — stage_lighting added (effectful); R17-9 wardrobe pack (2026-11-12): 351 -> 352 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 352 -> 353 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374 -> 375 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertEqual(totals["already_verified_in_handler"], 319)  # INTENTIONAL — R8-7 basic-pitch port (2026-11-10): 261 -> 262 — audio_to_midi (A4-filestat, see \u00a72c); R8-9 pypresence adoption (2026-11-10): 262 -> 264 — discord_presence (A5-sysreadback, see \u00a72c) + set_discord_client_id (A4-filestat, see \u00a72c); R9-2 rustic adoption (2026-11-10): 264 -> 267 — init_backup_repo (A5-sysreadback, see \u00a72c) + backup_files (A5-sysreadback, see \u00a72c) + restore_backup (A4-filestat, see \u00a72c); R9-3 whoosh port (2026-11-10): 267 -> 268 — index_files (A4-filestat, see \u00a72c); R9-4 mutagen port (2026-11-10): 268 -> 269 — sync_folders_two_way (A4-filestat, see §2c); R9-8 elodie port (2026-11-10): 269 -> 270 — organise_photos (A4-filestat, see §2c); R11-1 ollama port (2026-11-10): 270 -> 272 — ollama_pull + ollama_delete (A5-sysreadback, see §2c); R16-8 event-planning pack (2026-11-11): 272 -> 277 — five writing tools (A1-store, see §2c); R16-9 attic pack (2026-11-11): 277 -> 278 — home_inventory (A1-store, see §2c); R16-10 GreaterWMS pack (2026-11-11): 278 -> 279 — warehouse_stock (A1-store, see §2c); R17-1 bookrail pack (2026-11-11): 279 -> 280 — bookings (SQLite-trigger + re-read, see §2c); R17-3 go2rtc pack (2026-11-11): 280 -> 281 — ip_camera added (capture-gate + re-read/filestat, see §2c); R17-4 MCSManager pack (2026-11-11): 281 -> 282 — game_server added (A1-store + A5-sysreadback + process-leg, see §2c); R17-5 komga pack (2026-11-11): 282 -> 283 — ebook_library added (A1-store + A4-filestat, see §2c); R17-6 openstays pack (2026-11-11): 283 -> 284 — lodging added (SQLite-trigger + re-read, see §2c); R17-7 qlcplus pack (2026-11-12): 284 -> 285 — stage_lighting added (A1-store + send-return, see §2c); R17-9 wardrobe pack (2026-11-12): 285 -> 286 — wardrobe added (A1-store, see §2c); R18-1 stripe pack (2026-11-12): 286 -> 287 — billing added (A1-store, see §2c); R18-3 mcp-context-forge pack (2026-11-12): 287 -> 288; R18-4 a2a pack (2026-11-12): 288 -> 289; R18-5 dspy pack (2026-11-12): 289 -> 290; R18-6 fonoster pack (2026-11-12): 290 -> 291; R18-7 attendize pack (2026-11-12): 291 -> 292; R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_no_new_dependency(self):
        reqs = (ROOT / "requirements.txt").read_text()
        self.assertNotIn("yasb", reqs.lower())
        self.assertNotIn("status_strip", reqs)
        # The new modules import stdlib + PySide6 + tree modules only.
        for rel in ("core/status_strip.py", "gui/status_strip.py"):
            tree = ast.parse((ROOT / rel).read_text())
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imported.add(node.module.split(".")[0])
            self.assertNotIn("yasb", imported)
            self.assertNotIn("tkinter", imported)


class TestToolLogic(unittest.TestCase):
    def setUp(self):
        self._prev = get_status_strip_sink()
        set_status_strip_sink(None)

    def tearDown(self):
        set_status_strip_sink(self._prev)

    def test_no_sink_is_honest(self):
        result = strip_tool("status")
        self.assertIn("isn't running", result)
        self.assertNotIn("CPU", result)

    def test_show_hide_status_round_trip(self):
        sink = _FakeSink()
        set_status_strip_sink(sink)
        self.assertIn("now visible", strip_tool("show"))
        self.assertIn("CPU 23%", strip_tool("status"))
        self.assertIn("now hidden", strip_tool("hide"))
        self.assertIn("hidden", strip_tool("status"))
        self.assertEqual(sink.calls, ["show", "status", "hide", "status"])

    def test_show_that_did_not_appear_is_a_failure(self):
        def lying_sink(action):
            return {"ok": True, "visible": False, "text": "", "error": None}

        set_status_strip_sink(lying_sink)
        self.assertIn("didn't appear", strip_tool("show"))

    def test_hide_that_did_not_hide_is_a_failure(self):
        def stuck_sink(action):
            return {"ok": True, "visible": True, "text": "CPU 1%", "error": None}

        set_status_strip_sink(stuck_sink)
        self.assertIn("still showing", strip_tool("hide"))

    def test_bad_action(self):
        set_status_strip_sink(_FakeSink())
        self.assertIn("isn't one of them", strip_tool("bogus"))

    def test_raising_sink_is_honest(self):
        def boom(action):
            raise RuntimeError("sink exploded")

        set_status_strip_sink(boom)
        self.assertIn("didn't respond", strip_tool("status"))

    def test_non_dict_sink_is_honest(self):
        set_status_strip_sink(lambda action: "yes")
        self.assertIn("didn't answer clearly", strip_tool("status"))

    def test_funnel_end_to_end(self):
        sink = _FakeSink()
        set_status_strip_sink(sink)
        result = _registry.execute("status_strip", {"action": "show"})
        self.assertIn("now visible", str(result))

    def test_description_states_the_honest_scope(self):
        # The description names media/workspace only to deny them —
        # the strip must never promise what the tree cannot read.
        decl = next(
            d for d in _registry.get_declarations() if d["name"] == "status_strip"
        )
        desc = decl["description"].lower()
        self.assertIn("never media or workspace state", desc)
        self.assertNotIn("now playing", desc)
        self.assertNotIn("virtual desktop", desc)
        self.assertNotIn("pomodoro", desc)


class TestBoundarySource(unittest.TestCase):
    """Source scans, docstrings stripped first (the R8-4 lesson: the
    docstring explains the boundary in words the code must not use)."""

    @staticmethod
    def _code_without_docstrings(path: pathlib.Path) -> str:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc is not None and node.body:
                    first = node.body[0]
                    if isinstance(first, ast.Expr) and isinstance(
                        first.value, ast.Constant
                    ) and isinstance(first.value.value, str):
                        node.body = node.body[1:]
        return ast.unparse(tree)

    def test_no_per_app_polling_apis(self):
        for rel in ("core/status_strip.py", "gui/status_strip.py"):
            code = self._code_without_docstrings(ROOT / rel)
            lowered = code.lower()
            for banned in (
                "getforegroundwindow", "getwindowtext", "active window",
                "foreground window", "enumwindows", "win32gui",
            ):
                self.assertNotIn(banned, lowered, rel)

    def test_no_yasb_code_copied(self):
        # The strip's only YASB inheritance is the pattern, named in
        # the docstring — the code itself never names the library.
        for rel in ("core/status_strip.py", "gui/status_strip.py"):
            code = self._code_without_docstrings(ROOT / rel)
            self.assertNotIn("yasb", code.lower(), rel)
            self.assertNotIn("amnweb", code.lower(), rel)


class TestStripWidget(unittest.TestCase):
    def setUp(self):
        if not _qt_available():
            self.skipTest("PySide6 not installed on this interpreter")
        from PySide6.QtWidgets import QApplication

        self._app = QApplication.instance() or QApplication([])
        from gui.status_strip import StatusStrip

        self._Strip = StatusStrip

    def _make(self, sample=None):
        sampler = (lambda: dict(sample)) if sample is not None else None
        strip = self._Strip(sampler=sampler) if sampler else self._Strip()
        self.addCleanup(strip.deleteLater)
        return strip

    def test_numbers_behind_a_tap_from_fake_sampler(self):
        # UI rethink Build 3: the five cells are gone — the 8
        # numbers live behind a tap on the dot (popover) and on
        # the System Pulse page. The sampler values are the same.
        from PySide6.QtWidgets import QApplication

        strip = self._make(_fake_sample())
        strip.show()
        QApplication.processEvents()
        strip._tick()
        lines = strip.numbers_lines()
        self.assertEqual(len(lines), 8)
        for fragment in ("CPU 23%", "Memory 61%", "Disk 44%",
                         "Network 1.5 MB/s", "Battery 82%",
                         "Power Plugged", "Uptime 1h 00m",
                         "Processes 123"):
            self.assertIn(fragment, lines)
        strip.dot_button.click()
        QApplication.processEvents()
        self.assertTrue(strip._popover.isVisible())
        self.assertEqual(strip._popover.lines(), lines)
        strip.hide()
        QApplication.processEvents()

    def test_unknown_sensors_read_dash(self):
        from PySide6.QtWidgets import QApplication

        strip = self._make(_fake_sample(
            cpu=None, mem=None, disk=None, net_bps=None, battery_pct=None,
        ))
        strip.show()
        QApplication.processEvents()
        strip._tick()
        lines = strip.numbers_lines()
        for fragment in ("CPU —", "Memory —", "Disk —", "Network —",
                         "Battery —", "Power —"):
            self.assertIn(fragment, lines)
        strip.hide()
        QApplication.processEvents()

    def test_timer_only_ticks_while_visible(self):
        from PySide6.QtWidgets import QApplication

        strip = self._make(_fake_sample())
        self.assertFalse(strip._timer.isActive())
        strip.show()
        QApplication.processEvents()
        self.assertTrue(strip._timer.isActive())
        strip.hide()
        QApplication.processEvents()
        self.assertFalse(strip._timer.isActive())

    def test_latest_is_empty_before_first_tick(self):
        strip = self._make(_fake_sample())
        latest = strip.latest()
        self.assertTrue(all(v is None for v in latest.values()))

    def test_broken_sampler_reads_dash_not_crash(self):
        from PySide6.QtWidgets import QApplication

        def boom():
            raise RuntimeError("psutil exploded")

        strip = self._Strip(sampler=boom)
        self.addCleanup(strip.deleteLater)
        strip.show()
        QApplication.processEvents()
        strip._tick()
        self.assertIn("CPU —", strip.numbers_lines())
        self.assertEqual(strip.state_word(), "Ready")
        strip.hide()
        QApplication.processEvents()

    def test_docked_row_dot_and_word(self):
        # UI rethink Build 3: the strip is a docked 32 px row —
        # one dot + one word — not a floating always-on-top bar.
        # No drag, no persisted position, no window flags.
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        strip = self._make(_fake_sample())
        self.assertFalse(
            bool(strip.windowFlags() & Qt.WindowType.WindowStaysOnTopHint),
            "the docked strip must not float above other windows")
        self.assertEqual(strip.height(), 32)
        strip.show()
        QApplication.processEvents()
        strip._tick()
        self.assertEqual(strip.state_word(), "Ready")
        self.assertEqual(strip.word_button.text(), "Ready")
        self.assertEqual(strip.dot_button.text(), "●")
        strip.hide()
        QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
