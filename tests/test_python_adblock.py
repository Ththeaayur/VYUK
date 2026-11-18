"""R10-4 python-adblock port — suite.

Own stdlib ABP-subset matcher over a filter list the caller
supplies. The lab has no Chrome and no Playwright engine, so
every behavioural test drives synthetic list files built here;
nothing here claims Windows, real-Chrome or live-browser
behaviour. The interception half is STOPPED (the browser engine
it would plug into has no interception wiring), so no
test here touches a browser — the matcher is a read-only
classifier, and that is all the suite proves.
"""

import hashlib
import os
import pathlib
import tempfile
import unittest

from core.desktop import browser_adblock as ab
from core.funnel.tools_gate_egress import _LOCAL_ONLY_READS, _is_egress_read
from core.funnel.tools_gate_taint import _UNTRUSTED_PRODUCERS
from core.funnel.tools_registry import tools as _registry

ROOT = pathlib.Path(__file__).resolve().parent.parent

_BASIC_LIST = """[Adblock Plus 2.0]
! a comment line
||ads.example.com^
||tracker.net^$third-party
||static.example.org/banner/*$image
@@||example.com^$document
/ads/banner.js
example.org/popup
/ads[0-9]+\\.js/
example.com##.ad-banner
||cdn.example.com^$script,domain=example.com|~news.example.com
||first.example.com^$~third-party
"""


def _write_list(text=_BASIC_LIST):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    tmp.write(text)
    tmp.close()
    return tmp.name


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class TestMatcherBasics(unittest.TestCase):
    def setUp(self):
        self.list_file = _write_list()

    def tearDown(self):
        os.unlink(self.list_file)

    def test_domain_anchor_matches_host_and_subdomain(self):
        self.assertIn("Blocked by rule '||ads.example.com^'",
                      ab.check_url_blocked("https://ads.example.com/banner.js", self.list_file))
        self.assertIn("Blocked by rule '||ads.example.com^'",
                      ab.check_url_blocked("https://sub.ads.example.com/x.png", self.list_file))

    def test_domain_anchor_separator_blocks_lookalikes(self):
        # ||ads.example.com^ must NOT match a longer host that
        # merely starts with the domain (the ^ separator).
        self.assertIn("Not blocked",
                      ab.check_url_blocked("https://ads.example.com.evil.com/x", self.list_file))
        self.assertIn("Not blocked",
                      ab.check_url_blocked("https://ads.example.comfoo/x", self.list_file))

    def test_domain_anchor_is_case_insensitive(self):
        self.assertIn("Blocked by rule",
                      ab.check_url_blocked("https://ADS.EXAMPLE.COM/banner.js", self.list_file))

    def test_path_matching_is_case_sensitive(self):
        # ABP default: paths are case-sensitive.
        self.assertIn("Not blocked",
                      ab.check_url_blocked("https://example.org/POPUP.html", self.list_file))
        self.assertIn("Blocked by rule 'example.org/popup'",
                      ab.check_url_blocked("https://example.org/popup.html", self.list_file))

    def test_fragment_is_never_matched(self):
        self.assertIn("Blocked by rule '||ads.example.com^'",
                      ab.check_url_blocked("https://ads.example.com/x#frag", self.list_file))

    def test_left_anchor_and_substring(self):
        # The plain pattern /ads/banner.js is a substring rule —
        # it matches anywhere in the URL.
        self.assertIn("Blocked by rule '/ads/banner.js'",
                      ab.check_url_blocked("https://example.com/other/ads/banner.js",
                                           self.list_file))
        # A left-anchored rule matches only at the start.
        path = _write_list("|https://example.com/ads/\n")
        try:
            self.assertIn("Blocked by rule '|https://example.com/ads/'",
                          ab.check_url_blocked("https://example.com/ads/x.js", path))
            self.assertIn("Not blocked",
                          ab.check_url_blocked("https://example.com/other/ads/x.js", path))
        finally:
            os.unlink(path)

    def test_wildcard(self):
        self.assertIn("Blocked by rule '||static.example.org/banner/*$image'",
                      ab.check_url_blocked("https://static.example.org/banner/hero.jpg",
                                           self.list_file, resource_type="image"))

    def test_exception_beats_blocking_rule(self):
        self.assertIn("Allowed by exception rule '@@||example.com^$document'",
                      ab.check_url_blocked("https://example.com/page", self.list_file,
                                           resource_type="document"))

    def test_third_party_option(self):
        self.assertIn("Blocked by rule '||tracker.net^$third-party'",
                      ab.check_url_blocked("https://tracker.net/pixel.gif", self.list_file,
                                           source_domain="example.com"))
        self.assertIn("Not blocked",
                      ab.check_url_blocked("https://tracker.net/pixel.gif", self.list_file,
                                           source_domain="tracker.net"))

    def test_first_party_only_option(self):
        self.assertIn("Blocked by rule '||first.example.com^$~third-party'",
                      ab.check_url_blocked("https://first.example.com/home", self.list_file,
                                           source_domain="first.example.com"))
        self.assertIn("Not blocked",
                      ab.check_url_blocked("https://first.example.com/home", self.list_file,
                                           source_domain="other.com"))

    def test_domain_option_allow_and_exclude(self):
        self.assertIn("Blocked by rule '||cdn.example.com^$script,domain=example.com|~news.example.com'",
                      ab.check_url_blocked("https://cdn.example.com/lib.js", self.list_file,
                                           source_domain="example.com", resource_type="script"))
        self.assertIn("Not blocked",
                      ab.check_url_blocked("https://cdn.example.com/lib.js", self.list_file,
                                           source_domain="news.example.com", resource_type="script"))
        self.assertIn("Not blocked",
                      ab.check_url_blocked("https://cdn.example.com/lib.js", self.list_file,
                                           source_domain="other.com", resource_type="script"))

    def test_resource_type_restriction(self):
        self.assertIn("Not blocked",
                      ab.check_url_blocked("https://static.example.org/banner/hero.jpg",
                                           self.list_file, resource_type="script"))

    def test_domain_and_type_rules_need_context(self):
        # A $domain / $third-party / type-restricted rule never
        # matches a request that states no source domain / type.
        self.assertIn("Not blocked",
                      ab.check_url_blocked("https://tracker.net/pixel.gif", self.list_file))
        self.assertIn("Not blocked",
                      ab.check_url_blocked("https://static.example.org/banner/hero.jpg",
                                           self.list_file))

    def test_verdict_is_order_independent(self):
        # An exception listed AFTER the blocking rule still wins.
        reordered = "||ads.example.com^\n@@||ads.example.com^\n"
        path = _write_list(reordered)
        try:
            self.assertIn("Allowed by exception",
                          ab.check_url_blocked("https://ads.example.com/x", path))
        finally:
            os.unlink(path)

    def test_first_matching_blocking_rule_is_reported(self):
        two = "||ads.example.com^\n||ads.example.com/banner/*\n"
        path = _write_list(two)
        try:
            result = ab.check_url_blocked("https://ads.example.com/banner/x", path)
            self.assertIn("Blocked by rule '||ads.example.com^'", result)
        finally:
            os.unlink(path)

    def test_clean_url_not_blocked(self):
        self.assertIn("Not blocked",
                      ab.check_url_blocked("https://clean.example.net/page", self.list_file))


class TestListHandling(unittest.TestCase):
    def test_status_counts_and_fingerprint(self):
        path = _write_list()
        try:
            result = ab.filter_list_status(path)
            self.assertIn("7 blocking rules", result)
            self.assertIn("1 exception rules", result)
            self.assertIn("2 ignored", result)
            self.assertIn("regex 1", result)
            self.assertIn("element hiding 1", result)
            self.assertIn(_sha(path), result)
        finally:
            os.unlink(path)

    def test_unsupported_option_ignores_the_whole_rule(self):
        path = _write_list("||ads.example.com^$popup\n||ads.example.com^\n")
        try:
            # The $popup rule is ignored; the plain rule still blocks.
            result = ab.filter_list_status(path)
            self.assertIn("1 ignored", result)
            self.assertIn("unsupported options 1", result)
            self.assertIn("Blocked by rule '||ads.example.com^'",
                          ab.check_url_blocked("https://ads.example.com/x", path))
        finally:
            os.unlink(path)

    def test_regex_and_cosmetic_never_apply(self):
        # The regex and the ## rule are counted ignored — and the
        # URL they would have matched is NOT blocked by them.
        path = _write_list()
        try:
            self.assertIn("Not blocked",
                          ab.check_url_blocked("https://example.com/ads123.js", path))
        finally:
            os.unlink(path)

    def test_missing_list_file_is_an_honest_refusal(self):
        self.assertIn("No such list file",
                      ab.check_url_blocked("https://ads.example.com/x", "/nonexistent/list.txt"))
        self.assertIn("No such list file",
                      ab.filter_list_status("/nonexistent/list.txt"))

    def test_no_list_file_is_a_refusal_not_a_default(self):
        self.assertIn("Give me a filter list file",
                      ab.check_url_blocked("https://ads.example.com/x", None))
        self.assertIn("Give me a filter list file",
                      ab.filter_list_status(None))
        # The module ships with no list: no default-list constant.
        self.assertFalse(hasattr(ab, "_DEFAULT_LIST"))
        self.assertFalse(hasattr(ab, "_BUNDLED_LIST"))
        self.assertFalse(hasattr(ab, "_BUNDLED_RULES"))

    def test_directory_as_list_file_is_refused(self):
        self.assertIn("No such list file",
                      ab.check_url_blocked("https://ads.example.com/x", tempfile.gettempdir()))

    def test_bad_url_is_refused(self):
        path = _write_list()
        try:
            self.assertIn("not a URL I can check",
                          ab.check_url_blocked("not a url", path))
            self.assertIn("not a URL I can check",
                          ab.check_url_blocked("example.com/no-scheme", path))
        finally:
            os.unlink(path)

    def test_list_file_is_unchanged_by_a_read(self):
        path = _write_list()
        try:
            before = (_sha(path), os.stat(path).st_mtime_ns)
            ab.check_url_blocked("https://ads.example.com/x", path)
            ab.filter_list_status(path)
            self.assertEqual(before, (_sha(path), os.stat(path).st_mtime_ns))
        finally:
            os.unlink(path)


class TestTrustShape(unittest.TestCase):
    def test_no_network_or_browser_in_the_module(self):
        text = pathlib.Path(ab.__file__).read_text(encoding="utf-8")
        # The module docstring names the plug point (Playwright
        # page.route) to say the interception half is STOPPED —
        # so search the code only, after the docstring.
        code = text.split('"""', 2)[2]
        for banned in ("import socket", "import requests", "import httpx",
                       "urlopen", "import playwright",
                       "page.route"):
            self.assertNotIn(banned, code, banned)
        # urllib.parse is parsing only (no I/O) — it is allowed.
        self.assertIn("urllib.parse", text)

    def test_interception_half_is_stopped_in_the_docstring(self):
        self.assertIn("interception", ab.__doc__)
        self.assertIn("STOPPED", ab.__doc__)

    def test_local_only_reads_and_no_taint(self):
        for name in ("check_url_blocked", "filter_list_status"):
            self.assertIn(name, _LOCAL_ONLY_READS, name)
            self.assertFalse(_is_egress_read(name), name)
            self.assertIsNone(_UNTRUSTED_PRODUCERS.get(name), name)

    def test_both_tools_read_only_in_the_catalogue(self):
        text = (ROOT / "core" / "catalogue_groups_data.py").read_text(encoding="utf-8")
        self.assertIn('"check_url_blocked"', text)
        self.assertIn('"filter_list_status"', text)
        self.assertIn("R10-4 python-adblock port", text)


class TestRegistryPins(unittest.TestCase):
    def test_registry_counts(self):
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 756, len(decls))  # INTENTIONAL — R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added, read-only; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2078, total_params)  # INTENTIONAL — R10-4 python-adblock port (2026-11-10): 1598 -> 1603 — check_url_blocked adds 4 properties (url, list_file, source_domain, resource_type), filter_list_status adds 1 (list_file); R10-5 python-readability port (2026-11-10): 1603 -> 1605 — extract_article adds 2 properties (url, html); R11-1 ollama port (2026-11-10): 1605 -> 1607 — ollama_pull + ollama_delete add 1 property each (model); ollama_status adds 0; R16-8 event-planning pack (2026-11-11): 1607 -> 1640 — event_plan 7 + event_guest 7 + event_vendor 5 + event_budget 5 + event_milestone 5 + event_schedule 4 properties; R16-9 attic pack (2026-11-11): 1640 -> 1650 — home_inventory adds 10 properties (action, item, box, room, condition, value, purchased, photo, detail, quantity); R16-10 GreaterWMS pack (2026-11-11): 1650 -> 1658 — warehouse_stock adds 8 properties (action, item, location, from_location, to_location, quantity, capacity, detail); R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine

    def test_audit_totals(self):
        text = (ROOT / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        for needle in ('"total": 756', '"read": 382',  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): audit needles 717/379/338/272 -> 723/380/343/277 (confirm_gated 151 unchanged); R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
                       '"effectful": 374', '"confirm_gated": 164',  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): audit needles 717/379/338/272 -> 723/380/343/277 (confirm_gated 151 unchanged); R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-3 mcp-context-forge pack (2026-11-12): 154 -> 155; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-4 a2a pack (2026-11-12): 155 -> 156; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-5 dspy pack (2026-11-12): 156 -> 157; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-6 fonoster pack (2026-11-12): 157 -> 158; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine
                       '"already_verified_in_handler": 308',  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): audit needles 717/379/338/272 -> 723/380/343/277 (confirm_gated 151 unchanged); R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 287 -> 288; R18-4 a2a pack (2026-11-12): 288 -> 289; R18-5 dspy pack (2026-11-12): 289 -> 290; R18-6 fonoster pack (2026-11-12): 290 -> 291; R18-7 attendize pack (2026-11-12): 291 -> 292; R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine
                       '"browser_web": 36'):
            # INTENTIONAL — R10-4 python-adblock port (2026-11-10):
            # total 711 -> 713, read 375 -> 377, browser_web 33 -> 35
            # (check_url_blocked, filter_list_status added, read-only).
            # R11-1 ollama port (2026-11-10):
            # total 714 -> 717, read 378 -> 379, effectful 336 -> 338,
            # confirm 149 -> 151, already-verified 270 -> 272
            # (ollama_status added, read-only; ollama_pull +
            # ollama_delete added, confirm-gated, A5-sysreadback).
            self.assertIn(needle, text, needle)

    def test_version_stays_630(self):
        text = (ROOT / "core" / "version.py").read_text(encoding="utf-8")
        self.assertIn('VERSION = "6.3.0"', text)


if __name__ == "__main__":
    unittest.main()
