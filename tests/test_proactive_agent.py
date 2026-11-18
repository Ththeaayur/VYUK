"""R7-8 proactive-agent (ProactiveAgent pattern): when-to-intervene
scoring + 36-case benchmark, MEASUREMENT MODE ONLY.

Stdlib unittest only. The suite isolates the SQLite store by
patching core.proactivity.default_db_path to a temp dir (R7-7
lesson: a suite that exercises the real funnel dirties data/ —
this one never touches the live store).
"""

from __future__ import annotations

import ast
import json
import pathlib
import tempfile
import unittest

try:
    from core import proactivity as mod
    from core.funnel.tools_registry import tools as _registry
    from core.funnel.tools_gate import (
        _FREE_BY_DESIGN,
        _READ_ONLY_TOOLS,
    )
    from core import catalogue_snapshot
    from core.anticipation_gates import MIN_EVIDENCE_SAMPLES
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    raise unittest.SkipTest(f"core modules unavailable: {exc}")

_TREEROOT = pathlib.Path(__file__).resolve().parent.parent


def _ev(**kw):
    base = {"confidence": 0.0, "evidence_samples": 0}
    base.update(kw)
    return base


def _cand(text="Open the project folder.", **ev):
    return mod.InterventionCandidate(text=text, evidence=_ev(**ev))


class TestProactivityRegistry(unittest.TestCase):
    def test_registry_total_is_756(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        self.assertEqual(len(_registry.get_declarations()), 756)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine

    def test_new_tools_declared_with_exact_schemas(self):
        decls = {d["name"]: d for d in _registry.get_declarations()}
        want = {
            "score_intervention": ["candidate_text", "kind", "confidence",
                                   "evidence_samples", "explicit_request",
                                   "routine_due", "dismissed_recently",
                                   "user_mid_task", "watcher_kind",
                                   "prediction_available", "threshold"],
            "run_proactivity_bench": ["threshold"],
            "list_proactivity_scores": ["limit"],
            "proactivity_bench_results": ["limit"],
        }
        for name, props in want.items():
            self.assertIn(name, decls, f"{name} not registered")
            got = list((decls[name].get("parameters") or {})
                       .get("properties", {}))
            self.assertEqual(got, props, f"{name} schema mismatch")

    def test_schema_lint_total_is_2078(self):  # INTENTIONAL — Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine
        total = sum(len((d.get("parameters") or {}).get("properties") or {})
                    for d in _registry.get_declarations())
        self.assertEqual(total, 2078)  # INTENTIONAL: 1513 -> 1530 — score_intervention adds 11 properties (candidate_text, kind, confidence, evidence_samples, explicit_request, routine_due, dismissed_recently, user_mid_task, watcher_kind, prediction_available, threshold), run_proactivity_bench adds 1 property (threshold), list_proactivity_scores adds 1 property (limit), proactivity_bench_results adds 1 property (limit), R7-8 proactive-agent 2026-11-10; R8-1 fastembed port (2026-11-10): 1527 -> 1530 — semantic_search_knowledge adds 2 properties (query, limit), embed_knowledge adds 1 property (limit), knowledge_embedding_status adds 0; R8-3 sumy port (2026-11-10): 1530 -> 1532 — summarise_text adds 2 properties (text, max_sentences); R8-4 screen-break port (2026-11-10): 1532 -> 1538 — eye_rest_timer adds 3 properties (action, interval_minutes, snooze_minutes), breathing_guide adds 2 properties (pattern, cycles), log_glass adds 1 property (action); R8-5 magic-wormhole port (2026-11-10): 1538 -> 1544 — wormhole_offer adds 3 properties (action, file, expiry_minutes), wormhole_receive adds 3 properties (phrase, sender, destination), wormhole_status adds 0 properties; R8-6 yasb port (2026-11-10): 1544 -> 1545 — status_strip adds 1 property (action); R8-7 basic-pitch port (2026-11-10): 1545 -> 1551 — audio_to_midi adds 6 properties (audio_path, output, onset_threshold, frame_threshold, min_note_length_ms, tempo_bpm); audio_to_midi_status adds 0; R8-8 mftool port (2026-11-10): 1551 -> 1557 — fund_returns adds 5 properties (start_value, end_value, years, cashflows, currency), mutual_fund_nav adds 1 property (scheme); R8-9 pypresence adoption (2026-11-10): 1557 -> 1563 — discord_presence adds 5 properties (action, details, state, name, client_id); set_discord_client_id adds 1 property (client_id); R9-2 rustic adoption (2026-11-10): 1563 -> 1572 — init_backup_repo adds 1 property (repo_path); backup_files adds 3 (sources, repo_path, label); list_backup_snapshots adds 1 (repo_path); restore_backup adds 3 (snapshot, destination, repo_path); backup_status adds 1 (repo_path); R9-3 whoosh port (2026-11-10): 1572 -> 1576 — index_files adds 2 properties (folders, rebuild), search_indexed_files adds 2 properties (query, limit), disk_index_status adds 0; R9-4 mutagen port (2026-11-10): 1576 -> 1580 — sync_folders_two_way adds 4 properties (folder_a, folder_b, mirror_delete, dry_run); two_way_sync_status adds none; R9-8 elodie port (2026-11-10): 1580 -> 1585 — organise_photos adds 3 properties (source, destination, dry_run); preview_photo_organisation adds 2 properties (source, destination); R10-1 chrome-devtools-mcp port (2026-11-10): 1585 -> 1591 — browser_console adds 2 properties (seconds, target), browser_network adds 3 (seconds, target, max_entries), browser_performance adds 1 (target); R10-3 browser-history port (2026-11-10): 1591 -> 1598 — browser_history adds 4 properties (profile_dir, browser, days, limit), browser_bookmarks adds 3 (profile_dir, browser, limit); R10-4 python-adblock port (2026-11-10): 1598 -> 1603 — check_url_blocked adds 4 properties (url, list_file, source_domain, resource_type), filter_list_status adds 1 (list_file); R10-5 python-readability port (2026-11-10): 1603 -> 1605 — extract_article adds 2 properties (url, html); R11-1 ollama port (2026-11-10): 1605 -> 1607 — ollama_pull + ollama_delete add 1 property each (model); ollama_status adds 0; R16-8 event-planning pack (2026-11-11): 1607 -> 1640 — event_plan 7 + event_guest 7 + event_vendor 5 + event_budget 5 + event_milestone 5 + event_schedule 4 properties; R16-9 attic pack (2026-11-11): 1640 -> 1650 — home_inventory adds 10 properties (action, item, box, room, condition, value, purchased, photo, detail, quantity); R16-10 GreaterWMS pack (2026-11-11): 1650 -> 1658 — warehouse_stock adds 8 properties (action, item, location, from_location, to_location, quantity, capacity, detail); R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine

    def test_closed_count_is_755(self):  # INTENTIONAL — Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine
        closed = [d for d in _registry.get_declarations()
                  if (d.get("parameters") or {}).get("additionalProperties")
                  is False]
        self.assertEqual(len(closed), 755)  # INTENTIONAL — 676 -> 679: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10) added (additionalProperties False by the registry default, so the new tools are closed) (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): INTENTIONAL — closed count 679 -> 680: summarise_text added (additionalProperties False by the registry default, so the new tool is closed); R8-4 screen-break port (2026-11-10): INTENTIONAL — closed count 680 -> 683: eye_rest_timer, breathing_guide, log_glass added (additionalProperties False by the registry default, so the new tools are closed); R8-5 magic-wormhole port (2026-11-10): INTENTIONAL — closed count 683 -> 686: wormhole_offer, wormhole_receive, wormhole_status added (additionalProperties False by the registry default, so the new tools are closed); R8-6 yasb port (2026-11-10): INTENTIONAL — closed count 686 -> 687: status_strip added (additionalProperties False by the registry default, so the new tool is closed); R8-7 basic-pitch port (2026-11-10): INTENTIONAL — closed count 687 -> 689: audio_to_midi + audio_to_midi_status added (additionalProperties False by the registry default, so the new tools are closed); R8-8 mftool port (2026-11-10): INTENTIONAL — closed count 689 -> 691: fund_returns, mutual_fund_nav added (additionalProperties False by the registry default, so the new tools are closed); R8-9 pypresence adoption (2026-11-10): INTENTIONAL — closed count 691 -> 693: discord_presence + set_discord_client_id added (additionalProperties False by the registry default, so the new tools are closed); R9-2 rustic adoption (2026-11-10): INTENTIONAL — closed count 693 -> 698: init_backup_repo + backup_files + list_backup_snapshots + restore_backup + backup_status added (additionalProperties False by the registry default, so the new tools are closed); R9-3 whoosh port (2026-11-10): INTENTIONAL — closed count 698 -> 701: index_files, search_indexed_files, disk_index_status added (additionalProperties False by the registry default, so the new tools are closed); R9-4 mutagen port (2026-11-10): INTENTIONAL — closed count 701 -> 703: sync_folders_two_way, two_way_sync_status added (additionalProperties False by the registry default, so the new tools are closed); R9-8 elodie port (2026-11-10): 703 -> 705 — organise_photos, preview_photo_organisation added (additionalProperties False by the registry default, so the new tools are closed); R10-1 chrome-devtools-mcp port (2026-11-10): INTENTIONAL — closed count 705 -> 708: browser_console, browser_network, browser_performance added (additionalProperties False by the registry default, so the new tools are closed); R10-3 browser-history port (2026-11-10): INTENTIONAL — closed count 708 -> 710: browser_history, browser_bookmarks added (additionalProperties False by the registry default, so the new tools are closed); R10-4 python-adblock port (2026-11-10): INTENTIONAL — closed count 710 -> 712: check_url_blocked, filter_list_status added (additionalProperties False by the registry default, so the new tools are closed); R10-5 python-readability port (2026-11-10): INTENTIONAL — closed count 712 -> 713: extract_article added (additionalProperties False by the registry default, so the new tool is closed); R11-1 ollama port (2026-11-10): INTENTIONAL — closed count 713 -> 716: ollama_status (no parameters), ollama_pull + ollama_delete added (additionalProperties False by the registry default, so the new tools are closed); R16-8 event-planning pack (2026-11-11): INTENTIONAL — closed count 716 -> 722: six tools added (additionalProperties False by the registry default, so the new tools are closed); R16-9 attic pack (2026-11-11): INTENTIONAL — closed count 722 -> 723: home_inventory added (additionalProperties False by the registry default, so the new tool is closed); R16-10 GreaterWMS pack (2026-11-11): INTENTIONAL — closed count 723 -> 724: warehouse_stock added (additionalProperties False by the registry default, so the new tool is closed); R17-1 bookrail pack (2026-11-11): 724 -> 725 — bookings added (additionalProperties False by the registry default, so the new tool is closed); R17-2 trivy pack (2026-11-11): 725 -> 726 — security_scan added (additionalProperties False by the registry default, so the new tool is closed); R17-3 go2rtc pack (2026-11-11): 726 -> 727 — ip_camera added (additionalProperties False by the registry default, so the new tool is closed); R17-4 MCSManager pack (2026-11-11): 727 -> 728 — game_server added (additionalProperties False by the registry default, so the new tool is closed); R17-5 komga pack (2026-11-11): 728 -> 729 — ebook_library added (additionalProperties False by the registry default, so the new tool is closed); R17-6 openstays pack (2026-11-11): 729 -> 730 — lodging added (additionalProperties False by the registry default, so the new tool is closed); R17-7 qlcplus pack (2026-11-12): 730 -> 731 — stage_lighting added (additionalProperties False by the registry default, so the new tool is closed); R17-8 pvlib pack (2026-11-12): 731 -> 732 — solar_yield added (additionalProperties False by the registry default, so the new tool is closed); R17-9 wardrobe pack (2026-11-12): 732 -> 733 — wardrobe added (additionalProperties False by the registry default, so the new tool is closed); R18-1 stripe pack (2026-11-12): 733 -> 734 — billing added (additionalProperties False by the registry default, so the new tool is closed); R18-3 mcp-context-forge pack (2026-11-12): 734 -> 735 — mcp_registry added (additionalProperties False by the registry default, so the new tool is closed); R18-4 a2a pack (2026-11-12): 735 -> 736 — agent_call added (additionalProperties False by the registry default, so the new tool is closed); R18-5 dspy pack (2026-11-12): 736 -> 737 — prompt_tune added (additionalProperties False by the registry default, so the new tool is closed); R18-6 fonoster pack (2026-11-12): 737 -> 738 — voice_agent added (additionalProperties False by the registry default, so the new tool is closed); R18-7 attendize pack (2026-11-12): 738 -> 739 — event_tickets added (additionalProperties False by the registry default, so the new tool is closed); R18-8 ta pack (2026-11-12): 739 -> 740 — technical_analysis added (additionalProperties False by the registry default, so the new tool is closed); R18-10 timetrace pack (2026-11-12): 740 -> 741 — time_tracker added (additionalProperties False by the registry default, so the new tool is closed); R19-1 videolingo pack (2026-11-12): 741 -> 742 — audio_localise added (additionalProperties False by the registry default, so the new tool is closed); R19-2 ortools pack (2026-11-12): 742 -> 743 — optimise added (additionalProperties False by the registry default, so the new tool is closed); R19-3 aureuserp pack (2026-11-12): 743 -> 744 — erp added (additionalProperties False by the registry default, so the new tool is closed); R19-4 vidbee pack (2026-11-12): 744 -> 745 — media_chat added (additionalProperties False by the registry default, so the new tool is closed); R19-5 invoify pack (2026-11-12): 745 -> 746 — invoice added (additionalProperties False by the registry default, so the new tool is closed); R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine

    def test_grouped_count_is_752_and_in_system_apps(self):
        groups = catalogue_snapshot.GROUP_MEMBERS
        total = sum(len(v) for v in groups.values())
        self.assertEqual(total, 753)  # INTENTIONAL — 675 -> 678: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10) grouped in Mind (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 678 -> 679 — summarise_text grouped in Files & media; R8-4 screen-break port (2026-11-10): 679 -> 682 — eye_rest_timer, breathing_guide, log_glass grouped in Everyday skills; R8-5 magic-wormhole port (2026-11-10): 682 -> 685 — wormhole_offer, wormhole_receive, wormhole_status grouped in Files & media; R8-6 yasb port (2026-11-10): 685 -> 686 — status_strip grouped in System & apps; R8-7 basic-pitch port (2026-11-10): 686 -> 688 — audio_to_midi + audio_to_midi_status grouped in Play & create; R8-8 mftool port (2026-11-10): 688 -> 690 — fund_returns, mutual_fund_nav grouped in Everyday skills; R8-9 pypresence adoption (2026-11-10): 690 -> 692 — discord_presence + set_discord_client_id grouped in System & apps; R9-2 rustic adoption (2026-11-10): 692 -> 697 — init_backup_repo + backup_files + list_backup_snapshots + restore_backup + backup_status grouped in Files & media; R9-3 whoosh port (2026-11-10): 697 -> 700 — index_files, search_indexed_files, disk_index_status grouped in Files & media; R9-4 mutagen port (2026-11-10): 700 -> 702 — sync_folders_two_way, two_way_sync_status grouped in Files & media; R9-8 elodie port (2026-11-10): 702 -> 704 — organise_photos, preview_photo_organisation grouped in Files & media; R10-1 chrome-devtools-mcp port (2026-11-10): 704 -> 707 — browser_console, browser_network, browser_performance grouped in Web; R10-3 browser-history port (2026-11-10): 707 -> 709 — browser_history, browser_bookmarks grouped in Web; R10-4 python-adblock port (2026-11-10): 709 -> 711 — check_url_blocked, filter_list_status grouped in Web; R10-5 python-readability port (2026-11-10): 711 -> 712 — extract_article grouped in Web; R11-1 ollama port (2026-11-10): 712 -> 715 — ollama_status, ollama_pull, ollama_delete grouped in System & apps; R16-8 event-planning pack (2026-11-11): 715 -> 721 — six tools grouped in Everyday skills; R16-9 attic pack (2026-11-11): 721 -> 722 — home_inventory grouped in Everyday skills; R16-10 GreaterWMS pack (2026-11-11): 722 -> 723 — warehouse_stock grouped in Your world; R17-1 bookrail pack (2026-11-11): 723 -> 724 — bookings grouped in Your world; R17-2 trivy pack (2026-11-11): 724 -> 725 — security_scan grouped in System & apps; R17-3 go2rtc pack (2026-11-11): 725 -> 726 — ip_camera grouped in Hands & eyes; R17-4 MCSManager pack (2026-11-11): 726 -> 727 — game_server grouped in Hands & eyes; R17-5 komga pack (2026-11-11): 727 -> 728 — ebook_library grouped in Files & media; R17-6 openstays pack (2026-11-11): 728 -> 729 — lodging grouped in Your world; R17-7 qlcplus pack (2026-11-12): 729 -> 730 — stage_lighting grouped in Hands & eyes; R17-8 pvlib pack (2026-11-12): 730 -> 731 — solar_yield grouped in Your world; R17-9 wardrobe pack (2026-11-12): 731 -> 732 — wardrobe grouped in Your world; R18-1 stripe pack (2026-11-12): 732 -> 733 — billing grouped in Your world; R18-3 mcp-context-forge pack (2026-11-12): 733 -> 734 — mcp_registry grouped in Mind; R18-4 a2a pack (2026-11-12): 734 -> 735 — agent_call grouped in Mind; R18-5 dspy pack (2026-11-12): 735 -> 736 — prompt_tune grouped in Mind; R18-6 fonoster pack (2026-11-12): 736 -> 737 — voice_agent grouped in Your world; R18-7 attendize pack (2026-11-12): 737 -> 738 — event_tickets grouped in Everyday skills; R18-8 ta pack (2026-11-12): 738 -> 739 — technical_analysis grouped in Your world; R18-10 timetrace pack (2026-11-12): 739 -> 740 — time_tracker grouped in Everyday skills; R19-1 videolingo pack (2026-11-12): 740 -> 741 — audio_localise grouped in Play & create; R19-2 ortools pack (2026-11-12): 741 -> 742 — optimise grouped in Everyday skills; R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine
        names = groups.get("System & apps", ())
        for name in ("score_intervention", "run_proactivity_bench",
                     "list_proactivity_scores", "proactivity_bench_results"):
            self.assertIn(name, names)

    def test_gates(self):
        self.assertEqual(_FREE_BY_DESIGN.get("score_intervention"),
                         "proactivity")
        self.assertEqual(_FREE_BY_DESIGN.get("run_proactivity_bench"),
                         "proactivity")
        self.assertIn("list_proactivity_scores", _READ_ONLY_TOOLS)
        self.assertIn("proactivity_bench_results", _READ_ONLY_TOOLS)
        self.assertNotIn("score_intervention", _READ_ONLY_TOOLS)
        self.assertNotIn("run_proactivity_bench", _READ_ONLY_TOOLS)


class TestScorer(unittest.TestCase):
    def test_confident_prediction_intervenes(self):
        scored = mod.score_intervention(
            _cand(confidence=0.92, evidence_samples=20))
        self.assertEqual(scored.decision, "intervene")
        self.assertAlmostEqual(scored.score, 0.828, places=3)

    def test_low_confidence_stays_silent(self):
        scored = mod.score_intervention(
            _cand(confidence=0.30, evidence_samples=20))
        self.assertEqual(scored.decision, "silent")
        self.assertAlmostEqual(scored.score, 0.27, places=3)

    def test_mirrors_an1_threshold(self):
        # AN-1's own threshold is 0.70 confidence; 0.90 x 0.70 = 0.63
        # clears this scorer's 0.60 threshold on its own.
        at = mod.score_intervention(
            _cand(confidence=0.70, evidence_samples=5))
        self.assertEqual(at.decision, "intervene")
        # At 0.65 the score is 0.585 — silent without a second signal.
        below = mod.score_intervention(
            _cand(confidence=0.65, evidence_samples=20))
        self.assertEqual(below.decision, "silent")

    def test_silence_floor_below_five_samples(self):
        # Very confident but only 3 scored samples: the floor rules.
        scored = mod.score_intervention(
            _cand(confidence=0.90, evidence_samples=3))
        self.assertEqual(scored.decision, "silent")
        self.assertTrue(any("insufficient-evidence" in r
                            for r in scored.reasons))
        # Exactly at the floor (5) the same confidence intervenes.
        at_floor = mod.score_intervention(
            _cand(confidence=0.90, evidence_samples=5))
        self.assertEqual(at_floor.decision, "intervene")

    def test_explicit_request_bypasses_floor(self):
        scored = mod.score_intervention(_cand(
            confidence=0.30, evidence_samples=2, explicit_request=True))
        self.assertEqual(scored.decision, "intervene")
        self.assertAlmostEqual(scored.score, 0.72, places=3)

    def test_routine_due_bypasses_floor(self):
        scored = mod.score_intervention(_cand(
            confidence=0.50, evidence_samples=2, routine_due=True))
        self.assertEqual(scored.decision, "intervene")
        self.assertAlmostEqual(scored.score, 0.80, places=3)

    def test_dismissed_recently_suppresses(self):
        scored = mod.score_intervention(_cand(
            confidence=0.95, evidence_samples=30, dismissed_recently=True))
        self.assertEqual(scored.decision, "silent")
        self.assertAlmostEqual(scored.score, 0.505, places=3)

    def test_mid_task_suppresses_unless_explicit(self):
        mid = mod.score_intervention(_cand(
            confidence=0.90, evidence_samples=20, user_mid_task=True))
        self.assertEqual(mid.decision, "silent")
        asked = mod.score_intervention(_cand(
            confidence=0.60, evidence_samples=8, user_mid_task=True,
            explicit_request=True))
        self.assertEqual(asked.decision, "intervene")

    def test_watcher_weights(self):
        urgent = mod.score_intervention(_cand(
            confidence=0.72, evidence_samples=18, watcher_kind="battery"))
        self.assertEqual(urgent.decision, "intervene")
        soft = mod.score_intervention(_cand(
            confidence=0.60, evidence_samples=20, watcher_kind="downloads"))
        self.assertEqual(soft.decision, "silent")
        self.assertAlmostEqual(soft.score, 0.59, places=3)
        foreground = mod.score_intervention(_cand(
            confidence=0.45, evidence_samples=12, watcher_kind="foreground"))
        self.assertEqual(foreground.contributions.get("watcher"), 0.0)

    def test_no_prediction_available(self):
        scored = mod.score_intervention(_cand(
            confidence=0.0, evidence_samples=0, explicit_request=True,
            prediction_available=False))
        self.assertEqual(scored.decision, "silent")
        self.assertAlmostEqual(scored.score, 0.25, places=3)

    def test_threshold_parameter_respected(self):
        cand = _cand(confidence=0.80, evidence_samples=20)
        self.assertEqual(
            mod.score_intervention(cand, threshold=0.60).decision,
            "intervene")
        self.assertEqual(
            mod.score_intervention(cand, threshold=0.80).decision,
            "silent")

    def test_score_clamped_and_reasons_explain(self):
        scored = mod.score_intervention(_cand(
            confidence=0.95, evidence_samples=50, routine_due=True,
            watcher_kind="battery"))
        self.assertLessEqual(scored.score, 1.0)
        self.assertGreaterEqual(scored.score, 0.0)
        self.assertTrue(scored.reasons)
        self.assertTrue(any("confidence" in r for r in scored.reasons))
        self.assertTrue(any("routine due" in r for r in scored.reasons))
        # Contributions sum to the score (before clamping).
        self.assertAlmostEqual(
            min(1.0, sum(scored.contributions.values())),
            scored.score, places=3)

    def test_floor_constant_is_shared_with_an1(self):
        self.assertEqual(mod.MIN_EVIDENCE_SAMPLES, 5)
        self.assertEqual(mod.MIN_EVIDENCE_SAMPLES, MIN_EVIDENCE_SAMPLES)

    def test_evidence_from_prediction(self):
        class FakePrediction:
            confidence = 0.85

        cand = mod.evidence_from_prediction(
            FakePrediction(), 12, candidate_text="Open the folder.",
            watcher_kind="battery")
        self.assertEqual(cand.evidence["confidence"], 0.85)
        self.assertEqual(cand.evidence["evidence_samples"], 12)
        self.assertTrue(cand.evidence["prediction_available"])
        none_cand = mod.evidence_from_prediction(
            None, 0, candidate_text="Nothing yet.")
        self.assertEqual(none_cand.evidence["confidence"], 0.0)
        self.assertFalse(none_cand.evidence["prediction_available"])


class TestBench(unittest.TestCase):
    def test_bench_shape(self):
        self.assertEqual(len(mod.PROACTIVITY_BENCH), 36)
        positives = sum(1 for c in mod.PROACTIVITY_BENCH
                        if c.should_intervene)
        self.assertEqual(positives, 18)
        self.assertEqual(len(mod.PROACTIVITY_BENCH) - positives, 18)

    def test_bench_metrics_are_vyuks_own_number(self):
        # VYUK's measured number on VYUK's own bench — NOT the
        # ProactiveAgent paper's 0.918 F1 (their bench, their number).
        m = mod.score_bench()
        self.assertEqual(m["cases"], 36)
        self.assertEqual((m["tp"], m["fp"], m["tn"], m["fn"]),
                         (17, 2, 16, 1))
        self.assertAlmostEqual(m["precision"], 0.8947, places=4)
        self.assertAlmostEqual(m["recall"], 0.9444, places=4)
        self.assertAlmostEqual(m["f1"], 0.9189, places=4)
        self.assertAlmostEqual(m["accuracy"], 0.9167, places=4)

    def test_bench_misses_are_the_three_known_blind_spots(self):
        m = mod.score_bench()
        misses = sorted(c["name"] for c in m["per_case"]
                        if not c["correct"])
        self.assertEqual(misses, [
            "foreground-switch-to-calendar",
            "low-battery-not-critical",
            "trivial-routine",
        ])

    def test_bench_is_deterministic(self):
        self.assertEqual(mod.score_bench(), mod.score_bench())


class TestMeasurementModeOnly(unittest.TestCase):
    def test_module_adds_no_sensing_no_acting(self):
        src = (_TREEROOT / "core" / "proactivity.py").read_text(
            encoding="utf-8")
        # The docstring NAMES the forbidden things to say they are
        # NOT done here — strip it before checking the code, or the
        # check trips on its own prose (first-run lesson).
        doc = ast.get_docstring(ast.parse(src)) or ""
        code = src.replace(doc, "", 1)
        for forbidden in (
            "threading", "Thread(", "Timer(", "ActivityWatch",
            "_execute_tool", "planner_plans", "execute_fn",
            "watchers", "schedule.", "while True",
        ):
            self.assertNotIn(forbidden, code,
                             f"measurement-only module must not contain {forbidden!r}")
        # The module must SAY measurement-only, in its own docstring.
        self.assertIn("MEASUREMENT MODE ONLY", src)

    def test_scorer_is_pure_no_io(self):
        # score_intervention does no I/O: calling it creates no store.
        with tempfile.TemporaryDirectory(prefix="proactivity_pure_") as tmp:
            db = pathlib.Path(tmp) / "proactivity_runs.db"
            self.assertFalse(db.exists())
            mod.score_intervention(_cand(confidence=0.9,
                                         evidence_samples=20))
            self.assertFalse(db.exists())

    def test_imports_are_stdlib_plus_anticipation_gates(self):
        src = (_TREEROOT / "core" / "proactivity.py").read_text(
            encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(
                        alias.name.split(".")[0],
                        ("torch", "transformers", "tensorflow", "onnx"),
                        f"forbidden runtime import: {alias.name}")
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    # Relative import: only .anticipation_gates (the
                    # shared silence floor) and .config (DATA_DIR,
                    # the dagflow pattern) are allowed.
                    self.assertIn(
                        node.module,
                        ("anticipation_gates", "config"),
                        f"only .anticipation_gates/.config may be imported relatively: {node.module}")
                elif node.module and node.module.startswith("core"):
                    self.assertEqual(
                        node.module, "core.anticipation_gates",
                        f"only core.anticipation_gates may be imported: {node.module}")
                elif node.module:
                    self.assertNotIn(
                        node.module.split(".")[0],
                        ("torch", "transformers", "tensorflow", "onnx"))


class TestStoreAndHandlers(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="proactivity_db_")
        self._db = pathlib.Path(self._tmp.name) / "proactivity_runs.db"
        self._orig = mod.default_db_path
        mod.default_db_path = lambda: self._db  # noqa: E731

    def tearDown(self):
        mod.default_db_path = self._orig
        self._tmp.cleanup()

    def test_record_score_round_trips_and_re_reads(self):
        cand = _cand("Open the project folder.",
                     confidence=0.92, evidence_samples=20)
        scored = mod.score_intervention(cand)
        run_id = mod.record_score(scored, cand)
        rows = mod.list_scores(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], run_id)
        self.assertEqual(rows[0]["decision"], "intervene")
        self.assertAlmostEqual(rows[0]["score"], 0.828, places=3)

    def test_record_bench_run_round_trips(self):
        metrics = mod.score_bench()
        run_id = mod.record_bench_run(metrics)
        got = mod.get_bench_run(run_id)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got["f1"], 0.9189, places=4)
        self.assertEqual(len(got["per_case"]), 36)
        self.assertIsNone(mod.get_bench_run("no-such-id"))
        rows = mod.list_bench_runs(limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], run_id)

    def test_score_handler_end_to_end(self):
        out = mod.score_intervention_handler(
            candidate_text="Open the project folder.",
            confidence=0.92, evidence_samples=20)
        self.assertTrue(out["ok"])
        self.assertEqual(out["decision"], "intervene")
        self.assertIsNotNone(out["record_id"])
        self.assertIn("measurement only", out["message"].lower())
        listed = mod.list_proactivity_scores_handler(limit=10)
        self.assertTrue(listed["ok"])
        self.assertEqual(len(listed["scores"]), 1)

    def test_bench_handler_end_to_end(self):
        out = mod.run_proactivity_bench_handler()
        self.assertTrue(out["ok"])
        self.assertAlmostEqual(out["f1"], 0.9189, places=4)
        self.assertEqual(out["misses"], [
            "foreground-switch-to-calendar",
            "low-battery-not-critical",
            "trivial-routine",
        ])
        self.assertIsNotNone(out["record_id"])
        listed = mod.proactivity_bench_results_handler(limit=5)
        self.assertTrue(listed["ok"])
        self.assertEqual(len(listed["runs"]), 1)
        self.assertAlmostEqual(listed["runs"][0]["f1"], 0.9189, places=4)

    def test_handler_refusals(self):
        self.assertFalse(mod.score_intervention_handler(
            candidate_text="   ")["ok"])
        self.assertFalse(mod.score_intervention_handler(
            candidate_text="x", confidence="not-a-number")["ok"])
        self.assertFalse(mod.score_intervention_handler(
            candidate_text="x", threshold=0)["ok"])
        self.assertFalse(mod.score_intervention_handler(
            candidate_text="x", threshold=1.5)["ok"])
        self.assertFalse(mod.run_proactivity_bench_handler(
            threshold="bad")["ok"])
        self.assertFalse(mod.list_proactivity_scores_handler(limit=0)["ok"])
        self.assertFalse(mod.list_proactivity_scores_handler(limit=101)["ok"])
        self.assertFalse(mod.proactivity_bench_results_handler(limit=0)["ok"])

    def test_empty_lists_are_honest(self):
        listed = mod.list_proactivity_scores_handler(limit=10)
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["scores"], [])
        runs = mod.proactivity_bench_results_handler(limit=5)
        self.assertTrue(runs["ok"])
        self.assertEqual(runs["runs"], [])


if __name__ == "__main__":
    unittest.main()
