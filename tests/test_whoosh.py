"""R9-3 whoosh PORT — tests for the FTS5 disk content index.

The index is built by the module itself from temp folders, so no
operator-supplied artifact is needed and nothing here skips: every
test runs on both interpreters. All tests point the module at a temp
data dir (disk_index.DATA_DIR is read at call time), so the real
data/ is never touched.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

from core import disk_index
from core.disk_index import (
    _fts5_query,
    disk_index_status,
    fts5_available,
    index_files,
    search_indexed_files,
)
from core.funnel.tools_gate import _FREE_BY_DESIGN, _READ_ONLY_TOOLS
from core.funnel.tools_gate_egress import _LOCAL_ONLY_READS
from core.funnel.tools_gate_taint import _UNTRUSTED_PRODUCERS
from core.funnel.tools_registry import tools as _registry

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _DiskIndexTestCase(unittest.TestCase):
    """Base: a temp source folder + a temp data dir for the index."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = pathlib.Path(self._tmp.name)
        self.src = self.tmp / "docs"
        self.src.mkdir()
        self.data = self.tmp / "data"
        self.data.mkdir()
        self._orig_data_dir = disk_index.DATA_DIR
        disk_index.DATA_DIR = self.data
        self.addCleanup(setattr, disk_index, "DATA_DIR", self._orig_data_dir)

    def _write(self, name, text):
        path = self.src / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _seed(self):
        self._write("invoice.txt",
                    "The invoice total is 4200 rupees for the March order.\n")
        self._write("notes.md",
                    "# Meeting notes\nDiscuss the invoice and the delivery schedule.\n")
        self._write("code.py", "def total():\n    return 42\n")
        # .bin is excluded by extension (not counted); binary.txt
        # has a text extension but NUL bytes, so it counts as skipped.
        (self.src / "photo.bin").write_bytes(b"\x00\x01\x02" * 100)
        (self.src / "binary.txt").write_bytes(b"\x00\x01\x02" * 100)

    def _db_count(self):
        conn = sqlite3.connect(str(self.data / "disk_index.sqlite3"))
        try:
            return conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        finally:
            conn.close()


class HonestRefusalTests(_DiskIndexTestCase):
    def test_search_with_no_index_refuses(self):
        got = search_indexed_files("invoice")
        self.assertIn("Nothing is indexed yet", got)
        self.assertIn("index_files", got)

    def test_status_with_no_index_refuses(self):
        got = disk_index_status()
        self.assertIn("Nothing is indexed yet", got)

    def test_index_with_no_folders_refuses(self):
        got = index_files(folders="")
        self.assertIn("which folder to index", got)

    def test_index_with_missing_folder_refuses(self):
        got = index_files(folders=str(self.tmp / "no-such-folder"))
        self.assertIn("don't exist as folders", got)
        self.assertIn("no-such-folder", got)

    def test_search_empty_query_refuses(self):
        got = search_indexed_files("")
        self.assertIn("what text to look for", got)

    def test_search_no_match_is_honest(self):
        self._seed()
        index_files(folders=str(self.src))
        got = search_indexed_files("zzzqqq")
        self.assertIn("No indexed file mentions 'zzzqqq'", got)


class IndexBuildTests(_DiskIndexTestCase):
    def test_index_counts_and_skips_binary(self):
        self._seed()
        got = index_files(folders=str(self.src))
        self.assertIn("3 new", got)
        self.assertIn("1 skipped", got)
        self.assertIn("now holds 3 file(s)", got)
        self.assertEqual(self._db_count(), 3)

    def test_incremental_second_run_is_unchanged(self):
        self._seed()
        index_files(folders=str(self.src))
        got = index_files(folders=str(self.src))
        self.assertIn("0 file(s)", got)
        self.assertIn("3 unchanged", got)
        self.assertEqual(self._db_count(), 3)

    def test_edited_file_is_updated(self):
        self._seed()
        index_files(folders=str(self.src))
        self._write("invoice.txt", "invoice invoice invoice total\n")
        got = index_files(folders=str(self.src))
        self.assertIn("1 updated", got)
        self.assertIn("2 unchanged", got)

    def test_deleted_file_leaves_the_index(self):
        self._seed()
        index_files(folders=str(self.src))
        (self.src / "notes.md").unlink()
        got = index_files(folders=str(self.src))
        self.assertIn("1 removed", got)
        self.assertEqual(self._db_count(), 2)
        self.assertIn("No indexed file mentions 'delivery'",
                      search_indexed_files("delivery"))

    def test_rebuild_reindexes_everything(self):
        self._seed()
        index_files(folders=str(self.src))
        got = index_files(folders=str(self.src), rebuild=True)
        self.assertIn("3 new", got)
        self.assertEqual(self._db_count(), 3)

    def test_roots_are_remembered(self):
        self._seed()
        index_files(folders=str(self.src))
        roots = json.loads(
            (self.data / "disk_index_roots.json").read_text(
                encoding="utf-8"))
        self.assertEqual(roots, [str(self.src)])
        # A later run with no folders refreshes the remembered root.
        got = index_files(folders="")
        self.assertIn("3 unchanged", got)

    def test_one_bad_root_does_not_block_the_good_one(self):
        self._seed()
        other = self.tmp / "other"
        other.mkdir()
        (other / "a.txt").write_text("hello other\n", encoding="utf-8")
        got = index_files(
            folders=f"{self.src},{self.tmp / 'gone'}")
        self.assertIn("3 new", got)
        self.assertIn("don't exist right now", got)
        self.assertEqual(self._db_count(), 3)


class SearchTests(_DiskIndexTestCase):
    def test_search_returns_snippet_with_the_words(self):
        self._seed()
        index_files(folders=str(self.src))
        got = search_indexed_files("invoice total")
        self.assertIn("invoice.txt", got)
        self.assertIn("4200 rupees", got)

    def test_ranking_puts_the_denser_file_first(self):
        self._seed()
        self._write("invoice.txt",
                    "invoice invoice invoice total total total\n")
        index_files(folders=str(self.src))
        got = search_indexed_files("invoice")
        first = got.splitlines()[1]
        self.assertIn("invoice.txt", first)

    def test_search_is_and_not_phrase(self):
        self._seed()
        index_files(folders=str(self.src))
        # "delivery" (notes.md only) and "return" (code.py only)
        # never share a file; an AND search finds nothing while
        # each word alone finds its own file.
        self.assertIn("No indexed file mentions",
                      search_indexed_files("delivery return"))
        self.assertIn("notes.md", search_indexed_files("delivery"))
        self.assertIn("code.py", search_indexed_files("return"))

    def test_special_characters_are_data_not_syntax(self):
        self._seed()
        index_files(folders=str(self.src))
        got = search_indexed_files("invoice (total) *")
        self.assertIn("invoice.txt", got)

    def test_dead_hits_are_dropped_with_a_count(self):
        self._seed()
        index_files(folders=str(self.src))
        # Delete the file WITHOUT re-indexing: the search must not
        # return a path that no longer exists.
        (self.src / "notes.md").unlink()
        got = search_indexed_files("delivery")
        self.assertNotIn("notes.md", got)
        self.assertIn("no longer exists", got)

    def test_limit_is_respected(self):
        self._seed()
        for i in range(5):
            self._write(f"f{i}.txt", f"common word {i}\n")
        index_files(folders=str(self.src))
        got = search_indexed_files("common", limit=2)
        self.assertEqual(len(got.splitlines()) - 1, 2)

    def test_fts5_query_builder(self):
        self.assertEqual(_fts5_query("invoice total"),
                         '"invoice" AND "total"')
        self.assertEqual(_fts5_query("hello-world"), '"hello-world"')
        self.assertIsNone(_fts5_query("!!! ***"))
        self.assertIsNone(_fts5_query(""))


class StatusTests(_DiskIndexTestCase):
    def test_status_reports_roots_count_and_time(self):
        self._seed()
        index_files(folders=str(self.src))
        got = disk_index_status()
        self.assertIn("3 file(s)", got)
        self.assertIn(str(self.src), got)
        self.assertIn("last indexed", got)


class FunnelTests(_DiskIndexTestCase):
    def test_index_search_status_through_funnel(self):
        self._seed()
        built = _registry.execute(
            "index_files", {"folders": str(self.src)})
        self.assertIn("3 new", str(built), built)
        found = _registry.execute(
            "search_indexed_files", {"query": "invoice total"})
        self.assertIn("invoice.txt", str(found), found)
        status = _registry.execute("disk_index_status", {})
        self.assertIn("3 file(s)", str(status), status)

    def test_trust_classification(self):
        self.assertEqual(_FREE_BY_DESIGN.get("index_files"), "app-state")
        self.assertIn("search_indexed_files", _READ_ONLY_TOOLS)
        self.assertIn("disk_index_status", _READ_ONLY_TOOLS)
        self.assertIn("search_indexed_files", _LOCAL_ONLY_READS)
        self.assertIn("disk_index_status", _LOCAL_ONLY_READS)
        self.assertEqual(
            _UNTRUSTED_PRODUCERS.get("search_indexed_files"), "file")

    def test_declarations(self):
        decls = {d["name"]: d for d in _registry.get_declarations()}
        for name in ("index_files", "search_indexed_files",
                     "disk_index_status"):
            self.assertIn(name, decls)
        self.assertEqual(
            set(decls["index_files"]["parameters"]["properties"]),
            {"folders", "rebuild"})
        self.assertEqual(
            decls["index_files"]["parameters"]["required"], ["folders"])
        self.assertEqual(
            set(decls["search_indexed_files"]["parameters"]["properties"]),
            {"query", "limit"})
        self.assertEqual(
            decls["disk_index_status"]["parameters"]["properties"], {})

    def test_registry_counts(self):
        # R9-3 whoosh port (2026-11-10): registry 699 -> 702,
        # grouped 697 -> 700, schema-lint 1572 -> 1576 (+4:
        # index_files 2, search_indexed_files 2, disk_index_status 0).
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 769)  # INTENTIONAL — R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        total = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total, 2121)  # INTENTIONAL — R9-4 mutagen port (2026-11-10): 1576 -> 1580 — sync_folders_two_way adds 4 properties (folder_a, folder_b, mirror_delete, dry_run); two_way_sync_status adds none; R9-8 elodie port (2026-11-10): 1580 -> 1585 — organise_photos adds 3 properties (source, destination, dry_run); preview_photo_organisation adds 2 properties (source, destination); R10-1 chrome-devtools-mcp port (2026-11-10): 1585 -> 1591 — browser_console adds 2 properties (seconds, target), browser_network adds 3 (seconds, target, max_entries), browser_performance adds 1 (target); R10-3 browser-history port (2026-11-10): 1591 -> 1598 — browser_history adds 4 properties (profile_dir, browser, days, limit), browser_bookmarks adds 3 (profile_dir, browser, limit); R10-4 python-adblock port (2026-11-10): 1598 -> 1603 — check_url_blocked adds 4 properties (url, list_file, source_domain, resource_type), filter_list_status adds 1 (list_file); R10-5 python-readability port (2026-11-10): 1603 -> 1605 — extract_article adds 2 properties (url, html); R11-1 ollama port (2026-11-10): 1605 -> 1607 — ollama_pull + ollama_delete add 1 property each (model); ollama_status adds 0; R16-8 event-planning pack (2026-11-11): 1607 -> 1640 — event_plan 7 + event_guest 7 + event_vendor 5 + event_budget 5 + event_milestone 5 + event_schedule 4 properties; R16-9 attic pack (2026-11-11): 1640 -> 1650 — home_inventory adds 10 properties (action, item, box, room, condition, value, purchased, photo, detail, quantity); R16-10 GreaterWMS pack (2026-11-11): 1650 -> 1658 — warehouse_stock adds 8 properties (action, item, location, from_location, to_location, quantity, capacity, detail); R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169


class NoWhooshTests(unittest.TestCase):
    def test_no_whoosh_import_anywhere(self):
        # The module docstring names whoosh as the ported library
        # (like the screen-break docstring names tkinter) — the
        # check is on import statements, not the bare word.
        hits = []
        for path in list(ROOT.rglob("*.py")):
            if "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import whoosh", "from whoosh")):
                    hits.append(f"{path}:{stripped}")
        self.assertEqual(hits, [])

    def test_no_whoosh_in_requirements(self):
        req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("whoosh", req.lower())

    def test_module_imports_are_stdlib_plus_tree(self):
        import ast
        tree = ast.parse(
            (ROOT / "core" / "disk_index.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertTrue(
            imported <= {"json", "os", "sqlite3", "time", "pathlib",
                         "__future__", "config", "skills_plus_apps_files"},
            imported)

    def test_fts5_available_both_interpreters(self):
        self.assertTrue(fts5_available())


if __name__ == "__main__":
    unittest.main()
