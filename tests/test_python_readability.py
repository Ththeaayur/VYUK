"""R10-5 python-readability port — suite.

Own content-scoring article extractor over the tree's
already-declared lxml. The lab has no Chrome and no live
browser, so every behavioural test drives synthetic HTML built
here; nothing here claims Windows, real-Chrome or live-browser
behaviour. The URL form is tested with the fetch seams patched
(the same pattern skills_web_lookup uses); no test here touches
the network.
"""

import pathlib
import unittest
from unittest import mock

from core import article_extract as ae
from core.funnel.tools_gate_egress import _LOCAL_ONLY_READS, _is_egress_read
from core.funnel.tools_gate_taint import _UNTRUSTED_PRODUCERS
from core.funnel.tools_registry import tools as _registry
from core.skills_web_lookup import _page_text

ROOT = pathlib.Path(__file__).resolve().parent.parent

_ARTICLE_PAGE = """<html><head><title>Test Article — Example News</title></head><body>
<nav><p>Home About Contact Login Subscribe</p></nav>
<div class="cookie-banner"><p>We use cookies to improve your experience. Accept all cookies now.</p></div>
<div class="sidebar"><p>Related: Ten things you missed. Sponsored: Buy this watch today only.</p></div>
<main><article class="article-body">
<h1>The Mars Rover Finds Water Ice</h1>
<p>Scientists confirmed on Tuesday that the rover discovered water ice beneath the surface of the red planet, a finding that could reshape plans for future crewed missions and the search for past life.</p>
<p>The ice, buried less than a metre down in the northern plains, appears to be remarkably pure, according to the team, and stretches across an area roughly the size of a small country.</p>
<p>Further analysis will determine whether the deposit is stable enough to support drilling, and whether organic molecules survive alongside it in the frozen soil.</p>
</article></main>
<div class="ad-banner"><p>Amazing deals! Click here now for fifty percent off everything today only.</p></div>
<footer><p>Copyright 2026 Example News. All rights reserved. Privacy policy terms.</p></footer>
</body></html>"""

# No <main> or <article> at all: the article lives in divs, and
# so does the boilerplate. Only scoring can tell them apart.
_DIV_PAGE = """<html><head><title>Div-built article</title></head><body>
<div class="header"><p>Example News — Home News Sport Weather</p></div>
<div class="post-content">
<p>The city council voted on Thursday to approve the new tram line, ending two years of debate over the route through the old town and the cost of tunnelling under the river.</p>
<p>Supporters say the line will cut journey times across the city by a third, while critics warn the budget has already grown by forty percent since the first estimate.</p>
<p>Construction is due to start in the spring, with the first trams running within four years if the timetable holds and no further objections are lodged.</p>
<p>Residents along the route will be consulted on station design before work begins, the council said in a statement issued after the vote.</p>
</div>
<div class="sidebar">
<p>Most read: five recipes for winter. Sponsored: cheap flights to anywhere, book today.</p>
<p>Newsletter: sign up for our daily briefing and never miss a story again.</p>
</div>
<div class="related-posts">
<p>Related: the tram that never ran. Related: how other cities built theirs.</p>
</div>
</body></html>"""

_NO_ARTICLE_PAGE = """<html><head><title>Just a menu</title></head><body>
<nav><p>Home News Sport Weather Culture Business</p></nav>
<div class="links"><p>About us Contact Privacy Terms Careers</p></div>
</body></html>"""


class TestExtractorBasics(unittest.TestCase):
    def test_article_kept_boilerplate_dropped(self):
        result = ae.extract_article_html(_ARTICLE_PAGE)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["title"], "Test Article — Example News")
        self.assertIn("water ice beneath the surface", result["text"])
        self.assertIn("The Mars Rover Finds Water Ice", result["text"])
        for leaked in ("Ten things you missed", "Accept all cookies",
                       "fifty percent off", "Copyright 2026"):
            self.assertNotIn(leaked, result["text"], leaked)
        self.assertEqual(result["paragraphs_kept"], 4)
        self.assertIn("article-body", result["winner"])
        self.assertGreater(result["winner_score"], 0)

    def test_div_built_article_found_by_scoring(self):
        # No <main>/<article> anywhere: the winner must be the
        # scored div, not the first div.
        result = ae.extract_article_html(_DIV_PAGE)
        self.assertTrue(result["ok"], result)
        self.assertIn("tram line", result["text"])
        for leaked in ("Most read: five recipes", "cheap flights",
                       "Newsletter: sign up", "Related: the tram"):
            self.assertNotIn(leaked, result["text"], leaked)
        self.assertIn("post-content", result["winner"])

    def test_no_article_refuses_without_fallback(self):
        # Fail-closed: the answer must NOT contain the page's
        # text dressed up as an article.
        result = ae.extract_article_html(_NO_ARTICLE_PAGE)
        self.assertFalse(result["ok"])
        self.assertEqual(result["text"], "")
        answer = ae.extract_article(html=_NO_ARTICLE_PAGE)
        self.assertIn("Couldn't find an article body", answer)
        self.assertNotIn("Home News Sport", answer)

    def test_empty_and_unparseable_html_refuse(self):
        for bad in ("", "   ", "<html><body><p>unclosed"):
            result = ae.extract_article_html(bad)
            self.assertFalse(result["ok"], bad)
            self.assertEqual(result["text"], "")

    def test_scripts_never_appear_and_link_heavy_blocks_drop(self):
        html = """<html><head><title>Script test</title></head><body>
        <div class="content">
        <script>var secret = "stolen-token-123";</script>
        <p>Real article text here, long enough to score as a paragraph of genuine content for the reader.</p>
        <p>Second real paragraph, also long enough to score, carrying the story forward for the reader.</p>
        <p><a href="/a">link one</a> <a href="/b">link two</a> <a href="/c">link three</a> <a href="/d">link four</a></p>
        </div></body></html>"""
        result = ae.extract_article_html(html)
        self.assertTrue(result["ok"], result)
        self.assertNotIn("stolen-token-123", result["text"])
        self.assertNotIn("link one", result["text"])
        self.assertIn("Real article text", result["text"])

    def test_positive_class_rescues_negative_word(self):
        # class="content related" carries both signals: the
        # positive one wins, so the container is not dropped.
        html = """<html><head><title>Rescue test</title></head><body>
        <div class="content related">
        <p>First substantial paragraph of the rescued article, long enough to score as real content.</p>
        <p>Second substantial paragraph of the rescued article, long enough to score as real content.</p>
        <p>Third substantial paragraph of the rescued article, long enough to score as real content.</p>
        </div></body></html>"""
        result = ae.extract_article_html(html)
        self.assertTrue(result["ok"], result)
        self.assertIn("rescued article", result["text"])

    def test_oversized_html_refuses(self):
        big = "<html><body><p>" + "x" * 600_000 + "</p></body></html>"
        result = ae.extract_article_html(big)
        self.assertFalse(result["ok"])
        self.assertIn("cap", result["reason"])


class TestGapProof(unittest.TestCase):
    def test_page_text_keeps_what_the_extractor_drops(self):
        # The gap, reproduced: the existing tag-name strip keeps
        # every boilerplate paragraph; the extractor drops them.
        _title, text = _page_text(_ARTICLE_PAGE)
        self.assertIn("Ten things you missed", text)
        self.assertIn("Accept all cookies", text)
        self.assertIn("fifty percent off", text)
        result = ae.extract_article_html(_ARTICLE_PAGE)
        self.assertTrue(result["ok"])
        for leaked in ("Ten things you missed", "Accept all cookies",
                       "fifty percent off"):
            self.assertNotIn(leaked, result["text"], leaked)


class TestFunnel(unittest.TestCase):
    def test_neither_and_both_refused(self):
        self.assertIn("Give me the address", ae.extract_article())
        self.assertIn("not both",
                      ae.extract_article(url="https://example.com/x",
                                         html="<p>hi</p>"))

    def test_bad_url_refused(self):
        # The tree's URL normaliser is lenient, so a garbage
        # address reaches the fetch — which fails honestly and
        # says why, instead of pretending there is a page.
        self.assertIn("Couldn't fetch",
                      ae.extract_article(url="not a url at all ***"))

    def test_html_form_never_fetches(self):
        with mock.patch.object(ae, "_fetch",
                               side_effect=AssertionError("fetch called")):
            answer = ae.extract_article(html=_ARTICLE_PAGE)
        self.assertIn("water ice beneath the surface", answer)

    def test_url_form_uses_the_fetch_seams(self):
        with mock.patch.object(
                ae, "_normalise_url",
                return_value=("https://example.com/article", "example.com")), \
             mock.patch.object(
                 ae, "_fetch",
                 return_value=(_ARTICLE_PAGE.encode("utf-8"), mock.Mock(), None)), \
             mock.patch.object(ae, "_status_of", return_value=200):
            answer = ae.extract_article(url="https://example.com/article")
        self.assertIn("water ice beneath the surface", answer)
        self.assertIn("https://example.com/article", answer)

    def test_url_form_http_error_is_honest(self):
        with mock.patch.object(
                ae, "_normalise_url",
                return_value=("https://example.com/gone", "example.com")), \
             mock.patch.object(
                 ae, "_fetch",
                 return_value=(b"<html></html>", mock.Mock(), None)), \
             mock.patch.object(ae, "_status_of", return_value=404):
            answer = ae.extract_article(url="https://example.com/gone")
        self.assertIn("HTTP 404", answer)

    def test_url_form_fetch_failure_is_honest(self):
        with mock.patch.object(
                ae, "_normalise_url",
                return_value=("https://example.com/x", "example.com")), \
             mock.patch.object(
                 ae, "_fetch",
                 return_value=(None, None, "timed out after 10 s")):
            answer = ae.extract_article(url="https://example.com/x")
        self.assertIn("Couldn't fetch", answer)
        self.assertIn("timed out", answer)


class TestGates(unittest.TestCase):
    def test_tool_registered_and_read_only(self):
        names = {d.get("name") for d in _registry.get_declarations()}
        self.assertIn("extract_article", names)
        from core.funnel.tools_gate_readonly import _READ_ONLY_TOOLS
        self.assertIn("extract_article", _READ_ONLY_TOOLS)

    def test_taint_is_web_page_and_egress(self):
        # The article text is the page's own words: untrusted
        # producer "web page", and an egress read under taint
        # when the URL form fetches (the page_word_search shape).
        self.assertEqual(_UNTRUSTED_PRODUCERS.get("extract_article"),
                         "web page")
        self.assertNotIn("extract_article", _LOCAL_ONLY_READS)
        self.assertTrue(_is_egress_read("extract_article"))

    def test_tool_in_the_web_group(self):
        text = (ROOT / "core" / "catalogue_groups_data.py").read_text(
            encoding="utf-8")
        self.assertIn('"extract_article"', text)
        self.assertIn("R10-5 python-readability port", text)


class TestRegistryPins(unittest.TestCase):
    def test_registry_counts(self):
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 756, len(decls))  # INTENTIONAL — R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added, read-only; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2078, total_params)  # INTENTIONAL — R10-5 python-readability port (2026-11-10): 1603 -> 1605 — extract_article adds 2 properties (url, html); R11-1 ollama port (2026-11-10): 1605 -> 1607 — ollama_pull + ollama_delete add 1 property each (model); ollama_status adds 0; R16-8 event-planning pack (2026-11-11): 1607 -> 1640 — event_plan 7 + event_guest 7 + event_vendor 5 + event_budget 5 + event_milestone 5 + event_schedule 4 properties; R16-9 attic pack (2026-11-11): 1640 -> 1650 — home_inventory adds 10 properties (action, item, box, room, condition, value, purchased, photo, detail, quantity); R16-10 GreaterWMS pack (2026-11-11): 1650 -> 1658 — warehouse_stock adds 8 properties (action, item, location, from_location, to_location, quantity, capacity, detail); R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine

    def test_audit_totals(self):
        text = (ROOT / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        for needle in ('"total": 756', '"read": 382',  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): audit needles 717/379/338/272 -> 723/380/343/277 (confirm_gated 151 unchanged); R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
                       '"effectful": 374', '"confirm_gated": 164',  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): audit needles 717/379/338/272 -> 723/380/343/277 (confirm_gated 151 unchanged); R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-3 mcp-context-forge pack (2026-11-12): 154 -> 155; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-4 a2a pack (2026-11-12): 155 -> 156; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-5 dspy pack (2026-11-12): 156 -> 157; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-6 fonoster pack (2026-11-12): 157 -> 158; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine
                       '"already_verified_in_handler": 308',  # INTENTIONAL — R16-8 event-planning pack (2026-11-11): audit needles 717/379/338/272 -> 723/380/343/277 (confirm_gated 151 unchanged); R17-1 bookrail pack (2026-11-11): audit needles 725/345/279/129 -> 726/346/280/130 (bookings added); R18-3 mcp-context-forge pack (2026-11-12): 287 -> 288; R18-4 a2a pack (2026-11-12): 288 -> 289; R18-5 dspy pack (2026-11-12): 289 -> 290; R18-6 fonoster pack (2026-11-12): 290 -> 291; R18-7 attendize pack (2026-11-12): 291 -> 292; R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine
                       '"browser_web": 36'):
            # INTENTIONAL — R10-5 python-readability port (2026-11-10):
            # total 713 -> 714, read 377 -> 378, browser_web 35 -> 36
            # (extract_article added, read-only).
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
