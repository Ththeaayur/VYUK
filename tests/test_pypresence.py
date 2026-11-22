"""R8-9 pypresence — Discord Rich Presence as VYUK's own surface.

ADOPT, not port: pypresence 4.6.2, wheel 13,734 B, zero runtime
dependencies (dev extras only), MIT (repo LICENSE read from the repo
itself: "MIT License / Copyright (c) 2022 qwertyquerty"; the wheel's
own METADATA says License-Expression: MIT). The library is the
maintained implementation of Discord's local IPC (Windows named-pipe
discovery + handshake + opcode framing) — a port would re-derive that
behaviour blind, since no Discord client runs in this lab. Only
`Presence` is used; the full `Client` (OAuth2 authorize) is never
imported.

Pins: the client-ID discipline (operator-supplied, 17-19 digit
snowflake, no VYUK application ID ships, no hard-coded ID anywhere),
the opt-in shape (off by default, never auto-start, never re-applied
on boot, nothing set without an explicit request), the honest
refusals (no client ID configured, no Discord running, invalid ID),
the 128-character field clamp, the IPC ack read-back rule (a show is
only claimed when Discord acknowledged it), the funnel registration
+ gates + catalogue grouping, and the registry/schema-lint pins.
Real-IPC tests run only where a Discord IPC pipe exists — this lab
has none, so the show tests assert the honest refusal, never a
success.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import discord_presence
from core.discord_presence import (
    _CLIENT_ID_FILE,
    _clamp,
    _close_connection,
    _read_client_id,
    _valid_client_id,
    discord_presence as presence_fn,
    set_discord_client_id,
)
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
)
from core.funnel.tools_registry import tools as _registry

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ENV (debug pass): system python3 has no pypresence — the product
# refuses honestly there ("pypresence is not installed on this
# Python"), so the refusal paths these tests pin (no client ID /
# Discord not running) are only reachable where pypresence is
# installed (.venv-qt). Skip those tests on interpreters without
# it; the Qt run covers the behaviour.
try:
    import pypresence  # noqa: F401
    _HAS_PYPRESENCE = True
except Exception:  # noqa: BLE001
    _HAS_PYPRESENCE = False

# A valid-format Discord application ID that is not a real
# application: 18 digits, used only to exercise the refusal paths.
_FAKE_ID = "123456789012345678"


class _ConfigGuard:
    """Snapshot the operator config file around a test and restore it.

    The module stores the client ID in the tree's data/ dir, so a
    test that writes it must leave the file exactly as it found it
    (the batch's data restore covers residue, but the suite cleans
    up after itself).
    """

    def __init__(self):
        self._had = _CLIENT_ID_FILE.exists()
        self._bytes = _CLIENT_ID_FILE.read_bytes() if self._had else None

    def restore(self):
        _close_connection()
        if self._had:
            _CLIENT_ID_FILE.write_bytes(self._bytes)
        elif _CLIENT_ID_FILE.exists():
            _CLIENT_ID_FILE.unlink()


class TestClientIdDiscipline(unittest.TestCase):
    def setUp(self):
        self._guard = _ConfigGuard()

    def tearDown(self):
        self._guard.restore()

    def test_valid_snowflakes(self):
        for value in ("12345678901234567", "123456789012345678",
                      "1234567890123456789"):
            self.assertTrue(_valid_client_id(value), value)

    def test_invalid_ids_rejected(self):
        for value in ("", "123", "1234567890123456",  # 16 digits
                      "12345678901234567890",  # 20 digits
                      "12345678901234567a", "not-an-id", None, 123):
            self.assertFalse(_valid_client_id(value), repr(value))

    def test_set_rejects_bad_id_and_writes_nothing(self):
        out = set_discord_client_id("not-an-id")
        self.assertIn("17 to 19 digits", out)
        self.assertIsNone(_read_client_id())
        self.assertFalse(_CLIENT_ID_FILE.exists())

    def test_set_writes_and_re_reads(self):
        out = set_discord_client_id(_FAKE_ID)
        self.assertIn("saved", out)
        self.assertEqual(_read_client_id(), _FAKE_ID)
        data = json.loads(_CLIENT_ID_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data, {"client_id": _FAKE_ID})

    def test_set_drops_a_showing_presence(self):
        # A presence cannot be showing without Discord, but the
        # setter must still be safe to call with live state: fake
        # one in and check the setter clears it.
        discord_presence._CURRENT = {"name": "VYUK", "details": None,
                                     "state": None, "client_id": _FAKE_ID,
                                     "since": 0}
        out = set_discord_client_id(_FAKE_ID)
        self.assertIn("gone", out)
        self.assertIsNone(discord_presence._CURRENT)

    def test_no_hard_coded_client_id(self):
        src = (ROOT / "core" / "discord_presence.py").read_text(
            encoding="utf-8")
        self.assertIsNone(re.search(r"\d{17,}", src),
                          "a 17+ digit run in the module would be a "
                          "hard-coded client ID")

    def test_no_vyuk_application_id(self):
        # The module must say plainly that no VYUK application ID
        # ships, and must contain no application-ID literal: the
        # digit-run check above covers values, this covers intent.
        src = (ROOT / "core" / "discord_presence.py").read_text(
            encoding="utf-8")
        self.assertIn("VYUK ships no application ID of its own", src)


class TestOptInShape(unittest.TestCase):
    def setUp(self):
        self._guard = _ConfigGuard()

    def tearDown(self):
        self._guard.restore()

    def test_import_creates_no_state(self):
        self.assertIsNone(discord_presence._CONNECTION)
        self.assertIsNone(discord_presence._CURRENT)
        self.assertFalse(_CLIENT_ID_FILE.exists())

    def test_no_pypresence_import_at_module_level(self):
        src = (ROOT / "core" / "discord_presence.py").read_text(
            encoding="utf-8")
        # Strip the module docstring first (it names pypresence in prose).
        body = re.sub(r'^""".*?"""', "", src, count=1, flags=re.S)
        top_level = [line for line in body.splitlines()
                     if line.startswith("import ") or line.startswith("from ")]
        joined = "\n".join(top_level)
        self.assertNotIn("pypresence", joined)
        self.assertNotIn("socket", joined)
        self.assertNotIn("urllib", joined)
        self.assertNotIn("requests", joined)

    def test_unknown_action_refused(self):
        out = presence_fn(action="dance")
        self.assertIn("'show', 'clear' or 'status'", out)

    def test_clear_with_nothing_showing(self):
        out = presence_fn(action="clear")
        self.assertIn("nothing to clear", out)

    def test_status_is_a_pure_read(self):
        out = presence_fn(action="status")
        self.assertIn("No Discord presence is showing", out)
        self.assertIn("not configured", out)
        self.assertIn("no IPC pipe", out)
        self.assertIsNone(discord_presence._CONNECTION)
        self.assertIsNone(discord_presence._CURRENT)

    def test_field_clamp(self):
        self.assertEqual(_clamp("x" * 200), "x" * 128)
        self.assertEqual(_clamp("  hello  "), "hello")
        self.assertIsNone(_clamp(None))
        self.assertIsNone(_clamp("   "))


@unittest.skipUnless(_HAS_PYPRESENCE,
                     "pypresence not installed on this interpreter")
class TestHonestRefusals(unittest.TestCase):
    def setUp(self):
        self._guard = _ConfigGuard()

    def tearDown(self):
        self._guard.restore()

    def test_show_without_client_id(self):
        out = presence_fn(action="show", details="Testing")
        self.assertIn("No Discord application ID is configured", out)
        self.assertIn("set_discord_client_id", out)
        self.assertNotIn("is now showing", out)

    def test_show_with_bad_client_id_format(self):
        out = presence_fn(action="show", client_id="abc")
        self.assertIn("17 to 19 digits", out)
        self.assertNotIn("is now showing", out)

    def test_show_without_discord_refuses_honestly(self):
        # No Discord IPC pipe exists in this lab, so the real IPC
        # path must refuse — either "not running" (no pipe) or an
        # invalid-ID refusal (a pipe answered but the fake ID is
        # unknown). Either way nothing is claimed as showing.
        out = presence_fn(action="show", details="Testing",
                          client_id=_FAKE_ID)
        self.assertNotIn("is now showing", out)
        self.assertTrue(
            "isn't running" in out or "refused that application ID" in out,
            f"unexpected show result: {out!r}")
        self.assertIsNone(discord_presence._CONNECTION)
        self.assertIsNone(discord_presence._CURRENT)

    def test_show_with_stored_id_without_discord(self):
        set_discord_client_id(_FAKE_ID)
        out = presence_fn(action="show", details="Testing")
        self.assertNotIn("is now showing", out)
        self.assertTrue(
            "isn't running" in out or "refused that application ID" in out,
            f"unexpected show result: {out!r}")


class TestFunnelWiring(unittest.TestCase):
    def setUp(self):
        self._guard = _ConfigGuard()

    def tearDown(self):
        self._guard.restore()

    def test_both_tools_registered(self):
        names = {d["name"] for d in _registry.get_declarations()}
        self.assertIn("discord_presence", names)
        self.assertIn("set_discord_client_id", names)

    def test_gates(self):
        # Effectful, not read-only, not confirm-gated: the calls
        # themselves are the owner's explicit opt-in, so they are
        # free by design under the recorded classes.
        self.assertNotIn("discord_presence", _READ_ONLY_TOOLS)
        self.assertNotIn("set_discord_client_id", _READ_ONLY_TOOLS)
        self.assertNotIn("discord_presence", _CONFIRM_GATE_TOOLS)
        self.assertNotIn("set_discord_client_id", _CONFIRM_GATE_TOOLS)
        self.assertEqual(_FREE_BY_DESIGN.get("discord_presence"), "presence")
        self.assertEqual(_FREE_BY_DESIGN.get("set_discord_client_id"),
                         "app-state")

    def test_schemas(self):
        by_name = {d["name"]: d for d in _registry.get_declarations()}
        props = by_name["discord_presence"]["parameters"]["properties"]
        self.assertEqual(set(props),
                         {"action", "details", "state", "name", "client_id"})
        props = by_name["set_discord_client_id"]["parameters"]["properties"]
        self.assertEqual(set(props), {"client_id"})
        self.assertEqual(
            by_name["set_discord_client_id"]["parameters"]["required"],
            ["client_id"])

    def test_grouped_system_apps(self):
        # The catalogue snapshot (regenerated from
        # gui/skills_catalog_data.py) is the stdlib-only read of the
        # groups — the gui module itself needs PySide6.
        from core.catalogue_snapshot import GROUP_MEMBERS
        self.assertIn("discord_presence", GROUP_MEMBERS["System & apps"])
        self.assertIn("set_discord_client_id", GROUP_MEMBERS["System & apps"])

    @unittest.skipUnless(_HAS_PYPRESENCE,
                         "pypresence not installed on this interpreter")
    def test_execute_through_the_funnel(self):
        from core.funnel.tools_registry import tools
        out = tools.execute("discord_presence", {"action": "status"})
        self.assertIn("No Discord presence is showing", str(out))
        out = tools.execute("set_discord_client_id",
                            {"client_id": "not-an-id"})
        self.assertIn("17 to 19 digits", str(out))
        out = tools.execute("discord_presence",
                            {"action": "show", "details": "Testing"})
        self.assertIn("No Discord application ID is configured", str(out))


class TestRegistryPins(unittest.TestCase):
    def test_registry_total(self):
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 764)  # INTENTIONAL — 692 -> 694: discord_presence, set_discord_client_id added (R8-9 pypresence adoption, 2026-11-10); R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169

    def test_schema_lint_total(self):
        total = sum(len(d.get("parameters", {}).get("properties", {}))
                    for d in _registry.get_declarations())
        self.assertEqual(total, 2102)  # INTENTIONAL — 1557 -> 1563: discord_presence adds 5 properties (action, details, state, name, client_id), set_discord_client_id adds 1 property (client_id) (R8-9 pypresence adoption, 2026-11-10); R9-2 rustic adoption (2026-11-10): 1563 -> 1572 — init_backup_repo adds 1 property (repo_path); backup_files adds 3 (sources, repo_path, label); list_backup_snapshots adds 1 (repo_path); restore_backup adds 3 (snapshot, destination, repo_path); backup_status adds 1 (repo_path); R9-3 whoosh port (2026-11-10): 1572 -> 1576 — index_files adds 2 properties (folders, rebuild), search_indexed_files adds 2 properties (query, limit), disk_index_status adds 0; R9-4 mutagen port (2026-11-10): 1576 -> 1580 — sync_folders_two_way adds 4 properties (folder_a, folder_b, mirror_delete, dry_run); two_way_sync_status adds none; R9-8 elodie port (2026-11-10): 1580 -> 1585 — organise_photos adds 3 properties (source, destination, dry_run); preview_photo_organisation adds 2 properties (source, destination); R10-1 chrome-devtools-mcp port (2026-11-10): 1585 -> 1591 — browser_console adds 2 properties (seconds, target), browser_network adds 3 (seconds, target, max_entries), browser_performance adds 1 (target); R10-3 browser-history port (2026-11-10): 1591 -> 1598 — browser_history adds 4 properties (profile_dir, browser, days, limit), browser_bookmarks adds 3 (profile_dir, browser, limit); R10-4 python-adblock port (2026-11-10): 1598 -> 1603 — check_url_blocked adds 4 properties (url, list_file, source_domain, resource_type), filter_list_status adds 1 (list_file); R10-5 python-readability port (2026-11-10): 1603 -> 1605 — extract_article adds 2 properties (url, html); R11-1 ollama port (2026-11-10): 1605 -> 1607 — ollama_pull + ollama_delete add 1 property each (model); ollama_status adds 0; R16-8 event-planning pack (2026-11-11): 1607 -> 1640 — event_plan 7 + event_guest 7 + event_vendor 5 + event_budget 5 + event_milestone 5 + event_schedule 4 properties; R16-9 attic pack (2026-11-11): 1640 -> 1650 — home_inventory adds 10 properties (action, item, box, room, condition, value, purchased, photo, detail, quantity); R16-10 GreaterWMS pack (2026-11-11): 1650 -> 1658 — warehouse_stock adds 8 properties (action, item, location, from_location, to_location, quantity, capacity, detail); R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169

    def test_grouped_total(self):
        from core.catalogue_snapshot import GROUP_MEMBERS as SNAP_GROUPS
        grouped = sum(len(v) for v in SNAP_GROUPS.values())
        self.assertEqual(grouped, 761)  # INTENTIONAL — 690 -> 692: discord_presence, set_discord_client_id grouped in System & apps (R8-9 pypresence adoption, 2026-11-10); R9-2 rustic adoption (2026-11-10): 692 -> 697 — init_backup_repo + backup_files + list_backup_snapshots + restore_backup + backup_status grouped in Files & media; R9-3 whoosh port (2026-11-10): 697 -> 700 — index_files, search_indexed_files, disk_index_status grouped in Files & media; R9-4 mutagen port (2026-11-10): 700 -> 702 — sync_folders_two_way, two_way_sync_status grouped in Files & media; R9-8 elodie port (2026-11-10): 702 -> 704 — organise_photos, preview_photo_organisation grouped in Files & media; R10-1 chrome-devtools-mcp port (2026-11-10): 704 -> 707 — browser_console, browser_network, browser_performance grouped in Web; R10-3 browser-history port (2026-11-10): 707 -> 709 — browser_history, browser_bookmarks grouped in Web; R10-4 python-adblock port (2026-11-10): 709 -> 711 — check_url_blocked, filter_list_status grouped in Web; R10-5 python-readability port (2026-11-10): 711 -> 712 — extract_article grouped in Web; R11-1 ollama port (2026-11-10): 712 -> 715 — ollama_status, ollama_pull, ollama_delete grouped in System & apps; R16-8 event-planning pack (2026-11-11): 715 -> 721 — six tools grouped in Everyday skills; R16-9 attic pack (2026-11-11): 721 -> 722 — home_inventory grouped in Everyday skills; R16-10 GreaterWMS pack (2026-11-11): 722 -> 723 — warehouse_stock grouped in Your world; R17-1 bookrail pack (2026-11-11): 723 -> 724 — bookings grouped in Your world; R17-2 trivy pack (2026-11-11): 724 -> 725 — security_scan grouped in System & apps; R17-3 go2rtc pack (2026-11-11): 725 -> 726 — ip_camera grouped in Hands & eyes; R17-4 MCSManager pack (2026-11-11): 726 -> 727 — game_server grouped in Hands & eyes; R17-5 komga pack (2026-11-11): 727 -> 728 — ebook_library grouped in Files & media; R17-6 openstays pack (2026-11-11): 728 -> 729 — lodging grouped in Your world; R17-7 qlcplus pack (2026-11-12): 729 -> 730 — stage_lighting grouped in Hands & eyes; R17-8 pvlib pack (2026-11-12): 730 -> 731 — solar_yield grouped in Your world; R17-9 wardrobe pack (2026-11-12): 731 -> 732 — wardrobe grouped in Your world; R18-1 stripe pack (2026-11-12): 732 -> 733 — billing grouped in Your world; R18-3 mcp-context-forge pack (2026-11-12): 733 -> 734 — mcp_registry grouped in Mind; R18-4 a2a pack (2026-11-12): 734 -> 735 — agent_call grouped in Mind; R18-5 dspy pack (2026-11-12): 735 -> 736 — prompt_tune grouped in Mind; R18-6 fonoster pack (2026-11-12): 736 -> 737 — voice_agent grouped in Your world; R18-7 attendize pack (2026-11-12): 737 -> 738 — event_tickets grouped in Everyday skills; R18-8 ta pack (2026-11-12): 738 -> 739 — technical_analysis grouped in Your world; R18-10 timetrace pack (2026-11-12): 739 -> 740 — time_tracker grouped in Everyday skills; R19-1 videolingo pack (2026-11-12): 740 -> 741 — audio_localise grouped in Play & create; R19-2 ortools pack (2026-11-12): 741 -> 742 — optimise grouped in Everyday skills; R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169


if __name__ == "__main__":
    unittest.main()
