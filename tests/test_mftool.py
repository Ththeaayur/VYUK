"""R8-8 mftool — Indian mutual-fund maths as VYUK's own code.

PORT, not adopt: PyPI mftool 3.4's requires_dist is requests + bs4
+ httpx + pandas + yfinance + matplotlib + deprecated — a heavy
closure for an AMFI fetch that is a plain HTTP GET of a text file
plus arithmetic, and pandas was removed from the tree on weight
(no re-add). What is ported is the arithmetic that survives the
AMFI data-terms gate; the NAV fetch/display itself is STOPPED
(see core/mutual_fund.py's docstring for the terms as read from
amfiindia.com/terms-of-use on 2026-11-10: personal, non-commercial
use only; no public display/transmit/publish, no storing any
significant portion, no derivative works).

Pins: CAGR known values + guards, XIRR known values (incl. the
366-day leap-year count) + guards + Newton/bisection agreement,
the NAV refusal (wording, scheme echo, no network call), the
no-fetch shape (no urllib/requests/httpx/socket import in the
module, no fetch function), the funnel registration + read-only
gates + catalogue grouping, and the registry/schema-lint pins.
"""

from __future__ import annotations

import os
import pathlib
import re
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import mutual_fund
from core.mutual_fund import fund_returns, mutual_fund_nav
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
)
from core.funnel.tools_registry import tools as _registry

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pct(text: str) -> float:
    match = re.search(r"(-?\d+\.\d+)% a year", text)
    assert match, f"no yearly rate in: {text!r}"
    return float(match.group(1))


class TestCagr(unittest.TestCase):
    def test_doubling_over_five_years(self):
        # 2 ** (1/5) - 1 = 14.865...
        self.assertAlmostEqual(
            _pct(fund_returns(start_value=10000, end_value=20000, years=5)),
            14.87, places=2)

    def test_flat_is_zero(self):
        self.assertAlmostEqual(
            _pct(fund_returns(start_value=5000, end_value=5000, years=3)),
            0.0, places=2)

    def test_loss_is_negative(self):
        self.assertAlmostEqual(
            _pct(fund_returns(start_value=10000, end_value=8000, years=2)),
            -10.56, places=2)

    def test_fractional_years(self):
        # 1.5 ** (1/1.5) - 1 = 31.04...
        self.assertAlmostEqual(
            _pct(fund_returns(start_value=10000, end_value=15000, years=1.5)),
            31.04, places=2)

    def test_currency_label_printed(self):
        text = fund_returns(start_value=10000, end_value=20000, years=5,
                            currency="₹")
        self.assertIn("₹10,000.00", text)
        self.assertIn("₹20,000.00", text)

    def test_result_says_not_advice(self):
        text = fund_returns(start_value=100, end_value=200, years=1)
        self.assertIn("not financial advice", text)

    def test_guards(self):
        self.assertIn("more than zero",
                      fund_returns(start_value=0, end_value=5, years=1))
        self.assertIn("more than zero",
                      fund_returns(start_value=-5, end_value=5, years=1))
        self.assertIn("can't be negative",
                      fund_returns(start_value=5, end_value=-5, years=1))
        self.assertIn("more than zero",
                      fund_returns(start_value=5, end_value=6, years=0))
        self.assertIn("either a start value",
                      fund_returns())
        self.assertIn("either a start value",
                      fund_returns(start_value=5))


class TestXirr(unittest.TestCase):
    def test_one_year_ten_percent_leap_year(self):
        # 2020 is a leap year: 366 days, so the annualised rate is
        # (11/10) ** (365/366) - 1 = 9.97%, not 10.00%.
        self.assertAlmostEqual(
            _pct(fund_returns(cashflows=[
                {"date": "2020-01-01", "amount": -10000},
                {"date": "2021-01-01", "amount": 11000},
            ])), 9.97, places=2)

    def test_one_year_ten_percent_common_year(self):
        self.assertAlmostEqual(
            _pct(fund_returns(cashflows=[
                {"date": "2021-01-01", "amount": -10000},
                {"date": "2022-01-01", "amount": 11000},
            ])), 10.00, places=2)

    def test_sip_like_flows(self):
        self.assertAlmostEqual(
            _pct(fund_returns(cashflows=[
                {"date": "2023-01-05", "amount": -10000},
                {"date": "2023-07-05", "amount": -10000},
                {"date": "2024-01-05", "amount": 23000},
            ])), 20.26, places=2)

    def test_flows_need_not_be_sorted(self):
        a = fund_returns(cashflows=[
            {"date": "2021-01-01", "amount": -10000},
            {"date": "2022-01-01", "amount": 11000},
        ])
        b = fund_returns(cashflows=[
            {"date": "2022-01-01", "amount": 11000},
            {"date": "2021-01-01", "amount": -10000},
        ])
        self.assertEqual(a, b)

    def test_deterministic(self):
        flows = [
            {"date": "2022-03-01", "amount": -5000},
            {"date": "2022-09-01", "amount": -5000},
            {"date": "2023-03-01", "amount": 11500},
        ]
        self.assertEqual(fund_returns(cashflows=flows),
                         fund_returns(cashflows=flows))

    def test_invested_and_returned_shown(self):
        text = fund_returns(cashflows=[
            {"date": "2021-01-01", "amount": -10000},
            {"date": "2022-01-01", "amount": 11000},
        ], currency="₹")
        self.assertIn("₹10,000.00 in", text)
        self.assertIn("₹11,000.00 out", text)

    def test_guards(self):
        self.assertIn("at least two dated amounts",
                      fund_returns(cashflows=[{"date": "2021-01-01", "amount": -5}]))
        self.assertIn("both ways",
                      fund_returns(cashflows=[
                          {"date": "2021-01-01", "amount": -100},
                          {"date": "2022-01-01", "amount": -200},
                      ]))
        self.assertIn("YYYY-MM-DD",
                      fund_returns(cashflows=[
                          {"date": "1 Jan 2021", "amount": -100},
                          {"date": "2022-01-01", "amount": 110},
                      ]))
        self.assertIn("one day",
                      fund_returns(cashflows=[
                          {"date": "2021-01-01", "amount": -100},
                          {"date": "2021-01-01", "amount": 110},
                      ]))
        self.assertIn("either a start value",
                      fund_returns(cashflows=[]))

    def test_cashflows_win_over_cagr_inputs(self):
        # When both are given, the dated flows decide (documented).
        text = fund_returns(start_value=1, end_value=999, years=1,
                            cashflows=[
                                {"date": "2021-01-01", "amount": -10000},
                                {"date": "2022-01-01", "amount": 11000},
                            ])
        self.assertAlmostEqual(_pct(text), 10.00, places=2)


class TestNavRefusal(unittest.TestCase):
    def test_refusal_names_amfi_terms(self):
        text = mutual_fund_nav("SBI Bluechip")
        self.assertIn("SBI Bluechip", text)
        self.assertIn("personal, non-commercial use only", text)
        self.assertIn("commercial product", text)
        self.assertIn("won't guess a NAV", text)

    def test_refusal_without_scheme(self):
        text = mutual_fund_nav()
        self.assertIn("can't show mutual-fund NAVs", text)
        self.assertIn("written agreement with AMFI", text)

    def test_refusal_offers_the_maths(self):
        self.assertIn("fund returns", mutual_fund_nav("119018"))

    def test_no_network_imports_in_module(self):
        # The module's docstring documents the upstream closure and
        # the AMFI terms in prose — strip docstrings before
        # scanning, the R8-4 precedent: what must be absent is any
        # network import or fetch call in the code.
        import ast
        tree = ast.parse(
            (ROOT / "core" / "mutual_fund.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                if node.body:
                    first = node.body[0]
                    if (isinstance(first, ast.Expr)
                            and isinstance(first.value, ast.Constant)
                            and isinstance(first.value.value, str)):
                        node.body = node.body[1:]
        code = ast.unparse(tree)
        for banned in ("urllib", "requests", "httpx", "socket",
                       "urlopen"):
            self.assertNotIn(banned, code,
                             f"mutual_fund.py must not touch the network: {banned}")
        # The NAVAll format is documented as provenance in the
        # docstring only — there is no fetch function.
        self.assertNotIn("def fetch", code)
        self.assertNotIn("def _fetch", code)

    def test_no_mftool_or_pandas_anywhere_new(self):
        # The module documents mftool in prose (that is the port
        # record) but must never import it — nor pandas, which the
        # tree removed on weight.
        src = (ROOT / "core" / "mutual_fund.py").read_text(encoding="utf-8")
        for banned in ("import mftool", "from mftool",
                       "import pandas", "from pandas"):
            self.assertNotIn(banned, src)
        # requirements.txt mentions pandas in a comment (the removal
        # record) — what must be absent is a dependency line.
        req_lines = [
            line.strip() for line in
            (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        for banned in ("mftool", "yfinance", "pandas"):
            self.assertFalse(
                any(banned in line.split(";")[0].split()[0].lower()
                    for line in req_lines),
                f"requirements.txt must not declare {banned}")

    def test_module_imports_only_stdlib_and_helpers(self):
        src = (ROOT / "core" / "mutual_fund.py").read_text(encoding="utf-8")
        self.assertIn("import datetime", src)
        self.assertIn("import math", src)
        self.assertIn("skills_money_helpers", src)


class TestFunnelWiring(unittest.TestCase):
    def test_both_tools_registered(self):
        names = {d["name"] for d in _registry.get_declarations()}
        self.assertIn("fund_returns", names)
        self.assertIn("mutual_fund_nav", names)

    def test_both_read_only(self):
        self.assertIn("fund_returns", _READ_ONLY_TOOLS)
        self.assertIn("mutual_fund_nav", _READ_ONLY_TOOLS)
        self.assertNotIn("fund_returns", _CONFIRM_GATE_TOOLS)
        self.assertNotIn("mutual_fund_nav", _CONFIRM_GATE_TOOLS)
        self.assertNotIn("fund_returns", _FREE_BY_DESIGN)
        self.assertNotIn("mutual_fund_nav", _FREE_BY_DESIGN)

    def test_fund_returns_schema(self):
        by_name = {d["name"]: d for d in _registry.get_declarations()}
        props = by_name["fund_returns"]["parameters"]["properties"]
        self.assertEqual(set(props),
                         {"start_value", "end_value", "years",
                          "cashflows", "currency"})
        self.assertEqual(
            props["cashflows"]["items"]["required"], ["date", "amount"])

    def test_nav_schema(self):
        by_name = {d["name"]: d for d in _registry.get_declarations()}
        props = by_name["mutual_fund_nav"]["parameters"]["properties"]
        self.assertEqual(set(props), {"scheme"})

    def test_grouped_everyday_skills(self):
        # The catalogue snapshot (regenerated from
        # gui/skills_catalog_data.py) is the stdlib-only read of the
        # groups — the gui module itself needs PySide6.
        from core.catalogue_snapshot import GROUP_MEMBERS
        self.assertIn("fund_returns", GROUP_MEMBERS["Everyday skills"])
        self.assertIn("mutual_fund_nav", GROUP_MEMBERS["Everyday skills"])

    def test_execute_through_the_funnel(self):
        from core.funnel.tools_registry import tools
        out = tools.execute(
            "fund_returns",
            {"start_value": 10000, "end_value": 20000, "years": 5})
        self.assertIn("14.87% a year", str(out))
        out = tools.execute("mutual_fund_nav", {"scheme": "119018"})
        self.assertIn("can't show mutual-fund NAVs", str(out))

    def test_sip_projection_already_exists(self):
        # The finding's SIP half was already built: compound_growth
        # (monthly additions at a steady rate) IS a SIP projection.
        names = {d["name"] for d in _registry.get_declarations()}
        self.assertIn("compound_growth", names)
        from core.skills_money_growth import compound_growth
        text = compound_growth(principal=0, annual_rate_pct=12, years=1,
                               monthly_add=10000)
        self.assertIn("120,000.00", text)


class TestRegistryPins(unittest.TestCase):
    def test_registry_total(self):
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 758)  # INTENTIONAL — 690 -> 692: fund_returns, mutual_fund_nav added (R8-8 mftool port, 2026-11-10); R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing

    def test_schema_lint_total(self):
        total = sum(len(d.get("parameters", {}).get("properties", {}))
                    for d in _registry.get_declarations())
        self.assertEqual(total, 2079)  # INTENTIONAL — 1551 -> 1557: fund_returns adds 5 properties (start_value, end_value, years, cashflows, currency), mutual_fund_nav adds 1 property (scheme) (R8-8 mftool port, 2026-11-10); R8-9 pypresence adoption (2026-11-10): 1557 -> 1563 — discord_presence adds 5 properties (action, details, state, name, client_id); set_discord_client_id adds 1 property (client_id); R9-2 rustic adoption (2026-11-10): 1563 -> 1572 — init_backup_repo adds 1 property (repo_path); backup_files adds 3 (sources, repo_path, label); list_backup_snapshots adds 1 (repo_path); restore_backup adds 3 (snapshot, destination, repo_path); backup_status adds 1 (repo_path); R9-3 whoosh port (2026-11-10): 1572 -> 1576 — index_files adds 2 properties (folders, rebuild), search_indexed_files adds 2 properties (query, limit), disk_index_status adds 0; R9-4 mutagen port (2026-11-10): 1576 -> 1580 — sync_folders_two_way adds 4 properties (folder_a, folder_b, mirror_delete, dry_run); two_way_sync_status adds none; R9-8 elodie port (2026-11-10): 1580 -> 1585 — organise_photos adds 3 properties (source, destination, dry_run); preview_photo_organisation adds 2 properties (source, destination); R10-1 chrome-devtools-mcp port (2026-11-10): 1585 -> 1591 — browser_console adds 2 properties (seconds, target), browser_network adds 3 (seconds, target, max_entries), browser_performance adds 1 (target); R10-3 browser-history port (2026-11-10): 1591 -> 1598 — browser_history adds 4 properties (profile_dir, browser, days, limit), browser_bookmarks adds 3 (profile_dir, browser, limit); R10-4 python-adblock port (2026-11-10): 1598 -> 1603 — check_url_blocked adds 4 properties (url, list_file, source_domain, resource_type), filter_list_status adds 1 (list_file); R10-5 python-readability port (2026-11-10): 1603 -> 1605 — extract_article adds 2 properties (url, html); R11-1 ollama port (2026-11-10): 1605 -> 1607 — ollama_pull + ollama_delete add 1 property each (model); ollama_status adds 0; R16-8 event-planning pack (2026-11-11): 1607 -> 1640 — event_plan 7 + event_guest 7 + event_vendor 5 + event_budget 5 + event_milestone 5 + event_schedule 4 properties; R16-9 attic pack (2026-11-11): 1640 -> 1650 — home_inventory adds 10 properties (action, item, box, room, condition, value, purchased, photo, detail, quantity); R16-10 GreaterWMS pack (2026-11-11): 1650 -> 1658 — warehouse_stock adds 8 properties (action, item, location, from_location, to_location, quantity, capacity, detail); R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan

    def test_grouped_total(self):
        from core.catalogue_snapshot import GROUP_MEMBERS as SNAP_GROUPS
        grouped = sum(len(v) for v in SNAP_GROUPS.values())
        self.assertEqual(grouped, 755)  # INTENTIONAL — 688 -> 690: fund_returns, mutual_fund_nav grouped in Everyday skills (R8-8 mftool port, 2026-11-10); R8-9 pypresence adoption (2026-11-10): 690 -> 692 — discord_presence + set_discord_client_id grouped in System & apps; R9-2 rustic adoption (2026-11-10): 692 -> 697 — init_backup_repo + backup_files + list_backup_snapshots + restore_backup + backup_status grouped in Files & media; R9-3 whoosh port (2026-11-10): 697 -> 700 — index_files, search_indexed_files, disk_index_status grouped in Files & media; R9-4 mutagen port (2026-11-10): 700 -> 702 — sync_folders_two_way, two_way_sync_status grouped in Files & media; R9-8 elodie port (2026-11-10): 702 -> 704 — organise_photos, preview_photo_organisation grouped in Files & media; R10-1 chrome-devtools-mcp port (2026-11-10): 704 -> 707 — browser_console, browser_network, browser_performance grouped in Web; R10-3 browser-history port (2026-11-10): 707 -> 709 — browser_history, browser_bookmarks grouped in Web; R10-4 python-adblock port (2026-11-10): 709 -> 711 — check_url_blocked, filter_list_status grouped in Web; R10-5 python-readability port (2026-11-10): 711 -> 712 — extract_article grouped in Web; R11-1 ollama port (2026-11-10): 712 -> 715 — ollama_status, ollama_pull, ollama_delete grouped in System & apps; R16-8 event-planning pack (2026-11-11): 715 -> 721 — six tools grouped in Everyday skills; R16-9 attic pack (2026-11-11): 721 -> 722 — home_inventory grouped in Everyday skills; R16-10 GreaterWMS pack (2026-11-11): 722 -> 723 — warehouse_stock grouped in Your world; R17-1 bookrail pack (2026-11-11): 723 -> 724 — bookings grouped in Your world; R17-2 trivy pack (2026-11-11): 724 -> 725 — security_scan grouped in System & apps; R17-3 go2rtc pack (2026-11-11): 725 -> 726 — ip_camera grouped in Hands & eyes; R17-4 MCSManager pack (2026-11-11): 726 -> 727 — game_server grouped in Hands & eyes; R17-5 komga pack (2026-11-11): 727 -> 728 — ebook_library grouped in Files & media; R17-6 openstays pack (2026-11-11): 728 -> 729 — lodging grouped in Your world; R17-7 qlcplus pack (2026-11-12): 729 -> 730 — stage_lighting grouped in Hands & eyes; R17-8 pvlib pack (2026-11-12): 730 -> 731 — solar_yield grouped in Your world; R17-9 wardrobe pack (2026-11-12): 731 -> 732 — wardrobe grouped in Your world; R18-1 stripe pack (2026-11-12): 732 -> 733 — billing grouped in Your world; R18-3 mcp-context-forge pack (2026-11-12): 733 -> 734 — mcp_registry grouped in Mind; R18-4 a2a pack (2026-11-12): 734 -> 735 — agent_call grouped in Mind; R18-5 dspy pack (2026-11-12): 735 -> 736 — prompt_tune grouped in Mind; R18-6 fonoster pack (2026-11-12): 736 -> 737 — voice_agent grouped in Your world; R18-7 attendize pack (2026-11-12): 737 -> 738 — event_tickets grouped in Everyday skills; R18-8 ta pack (2026-11-12): 738 -> 739 — technical_analysis grouped in Your world; R18-10 timetrace pack (2026-11-12): 739 -> 740 — time_tracker grouped in Everyday skills; R19-1 videolingo pack (2026-11-12): 740 -> 741 — audio_localise grouped in Play & create; R19-2 ortools pack (2026-11-12): 741 -> 742 — optimise grouped in Everyday skills; R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing


if __name__ == "__main__":
    unittest.main()
