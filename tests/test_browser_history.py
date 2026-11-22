"""R10-3 browser-history port — suite.

Own stdlib sqlite3 + JSON reader over a COPY of a named profile's
History / places.sqlite / Bookmarks file. The lab has no Chrome and
no real browser profile, so every behavioural test drives synthetic
profile files built here; nothing here claims Windows or real-Chrome
behaviour.
"""

import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from core.desktop import browser_history as bh
from core.funnel.tools_registry import tools as _registry

_ENV = "VYUK_ALLOW_BROWSER_HISTORY"
_CHROME_EPOCH_OFFSET = 11644473600  # seconds between 1601-01-01 and 1970-01-01


def _chrome_us(when):
    """Naive local datetime -> Chrome-epoch microseconds."""
    unix = when.replace(tzinfo=datetime.timezone.utc).timestamp()
    return int((unix + _CHROME_EPOCH_OFFSET) * 1_000_000)


def _firefox_us(when):
    """Naive local datetime -> Unix-epoch microseconds."""
    return int(when.replace(tzinfo=datetime.timezone.utc).timestamp() * 1_000_000)


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _make_chromium_profile(root, now):
    """Synthetic Chrome profile dir: History (urls + visits) and
    Bookmarks JSON. Returns the profile dir."""
    profile = os.path.join(root, "Default")
    os.makedirs(profile)
    history = os.path.join(profile, "History")
    conn = sqlite3.connect(history)
    conn.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    conn.execute(
        "CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, "
        "visit_time INTEGER, visit_duration INTEGER)"
    )
    rows = [
        # (url, title, visit datetime, duration) — 2 days ago, kept
        ("https://example.com/recent", "Recent Example", now - datetime.timedelta(days=2), 5000),
        # 10 days ago — outside the default 7-day window
        ("https://example.com/old", "Old Example", now - datetime.timedelta(days=10), 8000),
        # zero duration — upstream's visit_duration > 0 drops it
        ("https://example.com/bounced", "Bounced", now - datetime.timedelta(days=1), 0),
        # no title — kept, shown as (no title)
        ("https://example.com/notitle", None, now - datetime.timedelta(days=3), 4000),
    ]
    for i, (url, title, when, duration) in enumerate(rows, start=1):
        conn.execute("INSERT INTO urls VALUES (?, ?, ?)", (i, url, title))
        conn.execute(
            "INSERT INTO visits VALUES (?, ?, ?, ?)",
            (i, i, _chrome_us(when), duration),
        )
    conn.commit()
    conn.close()
    bookmarks = {
        "roots": {
            "bookmark_bar": {
                "type": "folder",
                "name": "Bookmarks bar",
                "children": [
                    {
                        "type": "url",
                        "name": "Example Home",
                        "url": "https://example.com/",
                        "date_added": str(_chrome_us(now - datetime.timedelta(days=5))),
                    },
                    {
                        "type": "folder",
                        "name": "Docs",
                        "children": [
                            {
                                "type": "url",
                                "name": "Nested Doc",
                                "url": "https://example.com/doc",
                                "date_added": str(_chrome_us(now - datetime.timedelta(days=1))),
                            }
                        ],
                    },
                ],
            },
            "other": {"type": "folder", "name": "Other bookmarks", "children": []},
            "synced": {"type": "folder", "name": "Mobile bookmarks", "children": []},
        }
    }
    with open(os.path.join(profile, "Bookmarks"), "w", encoding="utf-8") as handle:
        json.dump(bookmarks, handle)
    return profile


def _make_firefox_profile(root, now):
    """Synthetic Firefox profile dir: places.sqlite only."""
    profile = os.path.join(root, "firefox-profile")
    os.makedirs(profile)
    path = os.path.join(profile, "places.sqlite")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    conn.execute(
        "CREATE TABLE moz_historyvisits (id INTEGER PRIMARY KEY, "
        "place_id INTEGER, visit_date INTEGER)"
    )
    conn.execute(
        "CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, parent INTEGER, "
        "fk INTEGER, title TEXT, dateAdded INTEGER)"
    )
    places = [
        (1, "https://example.com/ff", "Firefox Page"),
        (2, "https://example.com/ff-notitle", None),
        (3, "ftp://example.com/file", "Non-http"),
    ]
    for row in places:
        conn.execute("INSERT INTO moz_places VALUES (?, ?, ?)", row)
    conn.execute(
        "INSERT INTO moz_historyvisits VALUES (1, 1, ?)",
        (_firefox_us(now - datetime.timedelta(days=2)),),
    )
    conn.execute(
        "INSERT INTO moz_historyvisits VALUES (2, 2, ?)",
        (_firefox_us(now - datetime.timedelta(days=2)),),
    )
    conn.execute(
        "INSERT INTO moz_historyvisits VALUES (3, 3, ?)",
        (_firefox_us(now - datetime.timedelta(days=2)),),
    )
    # bookmark folder row (id 10) + bookmark row (id 11, fk -> place 1)
    conn.execute("INSERT INTO moz_bookmarks VALUES (10, 3, NULL, 'Reading', NULL)")
    conn.execute(
        "INSERT INTO moz_bookmarks VALUES (11, 10, 1, 'Firefox Page', ?)",
        (_firefox_us(now - datetime.timedelta(days=4)),),
    )
    conn.commit()
    conn.close()
    return profile


class BrowserHistoryGateTests(unittest.TestCase):
    def setUp(self):
        self._saved_env = os.environ.pop(_ENV, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.now = datetime.datetime.now().replace(microsecond=0)

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop(_ENV, None)
        else:
            os.environ[_ENV] = self._saved_env

    def _allow(self):
        os.environ[_ENV] = "1"

    def test_gate_off_refuses_and_touches_nothing(self):
        """Gate OFF (default): refusal text, no sqlite connect at all."""
        profile = _make_chromium_profile(self._tmp.name, self.now)
        live = os.path.join(profile, "History")
        before = (_sha(live), os.stat(live).st_mtime_ns)
        with mock.patch.object(
            bh.sqlite3, "connect", side_effect=AssertionError("must not connect")
        ):
            result = bh.browser_history(profile)
        self.assertIn("opt-in", result)
        self.assertIn("OFF", result)
        self.assertEqual(before, (_sha(live), os.stat(live).st_mtime_ns))

    def test_gate_on_via_env(self):
        self._allow()
        self.assertTrue(bh._browser_history_allowed())

    def test_gate_rejects_unrecognised_value(self):
        os.environ[_ENV] = "maybe"
        self.assertFalse(bh._browser_history_allowed())

    def test_missing_profile_dir_plain_message(self):
        self._allow()
        result = bh.browser_history(os.path.join(self._tmp.name, "nope"))
        self.assertIn("No such profile folder", result)

    def test_no_profile_arg_plain_message(self):
        self._allow()
        result = bh.browser_history(None)
        self.assertIn("profile folder named", result)

    def test_unknown_browser_plain_message(self):
        self._allow()
        profile = _make_chromium_profile(self._tmp.name, self.now)
        result = bh.browser_history(profile, browser="safari")
        self.assertIn("Unknown browser", result)

    def test_missing_history_file_plain_message(self):
        self._allow()
        empty = os.path.join(self._tmp.name, "Empty")
        os.makedirs(empty)
        result = bh.browser_history(empty)
        self.assertIn("No History file", result)


class BrowserHistoryReadTests(unittest.TestCase):
    def setUp(self):
        self._saved_env = os.environ.pop(_ENV, None)
        os.environ[_ENV] = "1"
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.now = datetime.datetime.now().replace(microsecond=0)

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop(_ENV, None)
        else:
            os.environ[_ENV] = self._saved_env

    def test_chromium_history_window_and_limit(self):
        profile = _make_chromium_profile(self._tmp.name, self.now)
        result = bh.browser_history(profile, days=7)
        self.assertIn("https://example.com/recent", result)
        self.assertIn("https://example.com/notitle", result)
        self.assertIn("(no title)", result)
        self.assertNotIn("example.com/old", result)  # outside 7 days
        self.assertNotIn("example.com/bounced", result)  # visit_duration 0
        wide = bh.browser_history(profile, days=30)
        self.assertIn("https://example.com/old", wide)
        limited = bh.browser_history(profile, days=30, limit=1)
        self.assertIn("1 visit(s)", limited)
        # newest first: the 1-day-old bounced visit is dropped, so the
        # newest kept row is the 2-day-old recent visit
        self.assertTrue(limited.splitlines()[1].startswith("20"))

    def test_chromium_history_live_file_untouched(self):
        profile = _make_chromium_profile(self._tmp.name, self.now)
        live = os.path.join(profile, "History")
        before = (_sha(live), os.stat(live).st_size, os.stat(live).st_mtime_ns)
        bh.browser_history(profile, days=30)
        bh.browser_bookmarks(profile)
        self.assertEqual(
            before, (_sha(live), os.stat(live).st_size, os.stat(live).st_mtime_ns)
        )

    def test_copy_discipline_sqlite_never_opens_live_file(self):
        """The sqlite connect path must be inside the temp dir, never
        inside the profile dir."""
        profile = _make_chromium_profile(self._tmp.name, self.now)
        seen = []
        real_connect = bh.sqlite3.connect

        def _spy(*args, **kwargs):
            seen.append(args[0] if args else kwargs.get("database", ""))
            return real_connect(*args, **kwargs)

        with mock.patch.object(bh.sqlite3, "connect", _spy):
            bh.browser_history(profile, days=30)
        self.assertTrue(seen, "expected at least one sqlite connect")
        tmpdir = tempfile.gettempdir()
        for path in seen:
            self.assertIn(tmpdir, path)
            self.assertNotIn(profile, path)
            self.assertIn("mode=ro", path)

    def test_copy_discipline_bookmarks_json_copied_first(self):
        """The Bookmarks JSON is copied to temp before it is read."""
        profile = _make_chromium_profile(self._tmp.name, self.now)
        copied = []
        real_copy = shutil.copy2

        def _spy(src, dst, *args, **kwargs):
            copied.append((str(src), str(dst)))
            return real_copy(src, dst, *args, **kwargs)

        with mock.patch.object(bh.shutil, "copy2", _spy):
            bh.browser_bookmarks(profile)
        self.assertTrue(copied, "expected at least one copy2")
        for src, dst in copied:
            self.assertIn("Bookmarks", src)
            self.assertIn(tempfile.gettempdir(), dst)
            self.assertNotIn(profile, dst)

    def test_firefox_history(self):
        profile = _make_firefox_profile(self._tmp.name, self.now)
        result = bh.browser_history(profile, browser="firefox", days=7)
        self.assertIn("https://example.com/ff", result)
        # Upstream-faithful: the Firefox SQL requires title IS NOT
        # NULL, so the untitled row is excluded (unlike Chromium,
        # whose SQL has no title filter).
        self.assertNotIn("ff-notitle", result)
        self.assertNotIn("ftp://", result)  # SQL filters to http%

    def test_chromium_bookmarks_nested_folders(self):
        profile = _make_chromium_profile(self._tmp.name, self.now)
        result = bh.browser_bookmarks(profile)
        self.assertIn("https://example.com/", result)
        self.assertIn("https://example.com/doc", result)
        self.assertIn("Docs", result)  # nested folder path is shown
        # newest first: the 1-day-old nested doc precedes the 5-day-old home
        self.assertLess(
            result.index("example.com/doc"), result.index("Example Home")
        )

    def test_firefox_bookmarks(self):
        profile = _make_firefox_profile(self._tmp.name, self.now)
        result = bh.browser_bookmarks(profile, browser="firefox")
        self.assertIn("https://example.com/ff", result)
        self.assertIn("Reading", result)

    def test_bookmarks_live_file_untouched(self):
        profile = _make_chromium_profile(self._tmp.name, self.now)
        live = os.path.join(profile, "Bookmarks")
        before = (_sha(live), os.stat(live).st_size, os.stat(live).st_mtime_ns)
        bh.browser_bookmarks(profile)
        self.assertEqual(
            before, (_sha(live), os.stat(live).st_size, os.stat(live).st_mtime_ns)
        )

    def test_clamps(self):
        profile = _make_chromium_profile(self._tmp.name, self.now)
        result = bh.browser_history(profile, days=0)
        self.assertIn("last 1 day(s)", result)
        result = bh.browser_history(profile, days=999)
        self.assertIn("last 90 day(s)", result)
        result = bh.browser_history(profile, days=30, limit=0)
        self.assertIn("1 visit(s)", result)

    def test_empty_window_plain_message(self):
        profile = _make_chromium_profile(self._tmp.name, self.now)
        # days=1: the newest kept visit is 2 days old
        result = bh.browser_history(profile, days=1)
        self.assertIn("No visits in the last 1 day(s)", result)


class BrowserHistoryWiringTests(unittest.TestCase):
    def test_tools_registered_and_read_only(self):
        decls = {d["name"]: d for d in _registry.get_declarations()}
        self.assertIn("browser_history", decls)
        self.assertIn("browser_bookmarks", decls)
        self.assertEqual(
            sorted(decls["browser_history"]["parameters"]["properties"]),
            ["browser", "days", "limit", "profile_dir"],
        )
        self.assertEqual(
            decls["browser_history"]["parameters"]["required"], ["profile_dir"]
        )
        self.assertEqual(
            sorted(decls["browser_bookmarks"]["parameters"]["properties"]),
            ["browser", "limit", "profile_dir"],
        )
        from core.funnel.tools_gate_readonly import _READ_ONLY_TOOLS

        self.assertIn("browser_history", _READ_ONLY_TOOLS)
        self.assertIn("browser_bookmarks", _READ_ONLY_TOOLS)
        from core.funnel.tools_gate_taint import _UNTRUSTED_PRODUCERS

        self.assertEqual(_UNTRUSTED_PRODUCERS.get("browser_history"), "web page")
        self.assertEqual(_UNTRUSTED_PRODUCERS.get("browser_bookmarks"), "web page")
        from core.funnel.tools_gate_egress import _LOCAL_ONLY_READS

        # Egress reads under taint (like browser_console): NOT local-only.
        self.assertNotIn("browser_history", _LOCAL_ONLY_READS)
        self.assertNotIn("browser_bookmarks", _LOCAL_ONLY_READS)

    def test_catalogue_and_snapshot(self):
        # Core snapshot (importable anywhere) + the GUI catalogue
        # (read via AST — gui needs PySide6, absent on system
        # python3; the test_domain_routers.py pattern, as in R10-1).
        import ast
        import json
        from pathlib import Path

        from core.catalogue_snapshot import GROUP_MEMBERS as snap_groups

        root = Path(__file__).resolve().parent.parent
        for name in ("browser_history", "browser_bookmarks"):
            self.assertIn(name, snap_groups.get("Web", ()))
        source = (root / "core" / "catalogue_groups_data.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        gui_groups = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(
                    node, ast.Assign) else [node.target]
                if len(targets) == 1 and getattr(
                        targets[0], "id", "") == "GROUP_MEMBERS":
                    gui_groups = ast.literal_eval(node.value)
        self.assertIsNotNone(gui_groups)
        for name in ("browser_history", "browser_bookmarks"):
            self.assertIn(name, gui_groups.get("Web", ()))
        snap = json.load(open("tests/_classification_snapshot.json"))
        self.assertEqual(
            snap["browser_history"], {"confirm": False, "free": None, "read": True}
        )
        self.assertEqual(
            snap["browser_bookmarks"], {"confirm": False, "free": None, "read": True}
        )

    def test_registry_and_schema_counts(self):
        self.assertEqual(len(_registry.get_declarations()), 764)  # INTENTIONAL — 709 -> 711: browser_history, browser_bookmarks added (R10-3 browser-history port, 2026-11-10); R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        total = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in _registry.get_declarations()
        )
        self.assertEqual(total, 2102)  # INTENTIONAL — 1591 -> 1598: browser_history adds 4 properties (profile_dir, browser, days, limit), browser_bookmarks adds 3 (profile_dir, browser, limit) (R10-3 browser-history port, 2026-11-10); R10-4 python-adblock port (2026-11-10): 1598 -> 1603 — check_url_blocked adds 4 properties (url, list_file, source_domain, resource_type), filter_list_status adds 1 (list_file); R10-5 python-readability port (2026-11-10): 1603 -> 1605 — extract_article adds 2 properties (url, html); R11-1 ollama port (2026-11-10): 1605 -> 1607 — ollama_pull + ollama_delete add 1 property each (model); ollama_status adds 0; R16-8 event-planning pack (2026-11-11): 1607 -> 1640 — event_plan 7 + event_guest 7 + event_vendor 5 + event_budget 5 + event_milestone 5 + event_schedule 4 properties; R16-9 attic pack (2026-11-11): 1640 -> 1650 — home_inventory adds 10 properties (action, item, box, room, condition, value, purchased, photo, detail, quantity); R16-10 GreaterWMS pack (2026-11-11): 1650 -> 1658 — warehouse_stock adds 8 properties (action, item, location, from_location, to_location, quantity, capacity, detail); R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169

    def test_no_adoption_and_no_auto_discovery(self):
        """Nothing adopted: no browser_history import, no default
        profile paths anywhere in the module, no watcher, SELECT
        only."""
        import pathlib

        src = pathlib.Path("core/desktop/browser_history.py").read_text()
        self.assertNotIn("import browser_history", src)
        self.assertNotIn("from browser_history", src)
        for banned in ("AppData", "Local\\Google", ".config/google-chrome",
                       "Library/Application Support", "Path.home()"):
            self.assertNotIn(banned, src)
        for banned in ("threading", "watchdog", "Observer", "while True"):
            self.assertNotIn(banned, src)
        for verb in ("INSERT", "UPDATE", "DELETE", "DROP TABLE"):
            self.assertNotIn(verb, src)
        # No THIRD_PARTY row and no requirements entry for the upstream.
        third = open("THIRD_PARTY_LICENSES.md").read()
        self.assertNotIn("browser-history", third)
        req = open("requirements.txt").read()
        self.assertNotIn("browser-history", req)
        self.assertNotIn("browser_history", req)

    def test_module_docstring_states_discipline(self):
        import pathlib

        src = pathlib.Path("core/desktop/browser_history.py").read_text()
        for phrase in (
            "Apache-2.0",
            "never guessed",
            "OFF by default",
            "copy",
            "never opened",
        ):
            self.assertIn(phrase, src)


if __name__ == "__main__":
    unittest.main()
