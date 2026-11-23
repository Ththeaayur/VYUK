"""R17-5 komga rebuild — ebooks and comics. Fake books only: real
EPUB/CBZ fixtures built in the test, a real in-process HTTP server
on 127.0.0.1 for the OPDS/KOReader endpoints, no real e-reader, no
network beyond loopback, no downloads.
"""
import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".pkgroot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import ebook_library as el
from core.funnel.tools_gate_free import _FREE_BY_DESIGN
from core.funnel.tools_gate_readclass import _is_read_action
from core.funnel.tools_registry import tools
from core.memory.memory import MemoryStore
from core.skills_admin_helpers import _store as _helpers_store
import core.skills_admin_helpers as helpers

_FAKE_JPEG = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01"
              b"\x00\x00" + b"\x00" * 100)


def _make_epub(path, title="Test Book", author="Test Author",
               series="Test Series", series_index="2", spine_n=3,
               with_cover=True):
    opf_items = []
    spine_refs = []
    for i in range(1, spine_n + 1):
        opf_items.append(
            f'<item id="chap{i}" href="chap{i}.xhtml" '
            f'media-type="application/xhtml+xml"/>')
        spine_refs.append(f'<itemref idref="chap{i}"/>')
    if with_cover:
        opf_items.append(
            '<item id="cover" href="cover.jpg" media-type="image/jpeg" '
            'properties="cover-image"/>')
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="3.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>{title}</dc:title>
<dc:creator>{author}</dc:creator>
<dc:language>en</dc:language>
<dc:identifier id="uid">urn:test:{title}</dc:identifier>
<meta name="calibre:series" content="{series}"/>
<meta name="calibre:series_index" content="{series_index}"/>
</metadata>
<manifest>{''.join(opf_items)}</manifest>
<spine>{''.join(spine_refs)}</spine>
</package>"""
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip",
                    compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        for i in range(1, spine_n + 1):
            zf.writestr(f"OEBPS/chap{i}.xhtml",
                        f"<html><body><p>Chapter {i}</p></body></html>")
        if with_cover:
            zf.writestr("OEBPS/cover.jpg", _FAKE_JPEG)


def _make_cbz(path, title="Test Comic", series="Test Series",
              pages=5, with_comicinfo=True):
    with zipfile.ZipFile(path, "w") as zf:
        if with_comicinfo:
            zf.writestr("ComicInfo.xml",
                        f"<?xml version=\"1.0\"?><ComicInfo><Title>{title}"
                        f"</Title><Series>{series}</Series>"
                        f"<Writer>Comic Author</Writer><Number>1</Number>"
                        f"</ComicInfo>")
        for i in range(1, pages + 1):
            zf.writestr(f"{i:03d}.jpg", _FAKE_JPEG)


class KomgaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = MemoryStore(
            db_path=str(self.root / "facts.db"))
        self._orig_helpers_store = _helpers_store
        helpers._store = lambda: self.store
        self._orig_el_store = el._store
        el._store = lambda: self.store
        self._orig_lan = el._lan_address
        el._lan_address = lambda: "127.0.0.1"
        self.book_dir = self.root / "books"
        self.book_dir.mkdir()
        self.epub = self.book_dir / "test-book.epub"
        _make_epub(self.epub)
        self.cbz = self.book_dir / "test-comic.cbz"
        _make_cbz(self.cbz)
        (self.book_dir / "ignored.cbr").write_bytes(b"RAR!")
        (self.book_dir / "ignored.pdf").write_bytes(b"%PDF-1.4")
        (self.book_dir / "notes.txt").write_text("not a book")
        self._serving = False

    def tearDown(self):
        try:
            if el._SERVER.is_running():
                el.ebook_library(action="stop")
        except Exception:  # noqa: BLE001 - teardown is best effort
            pass
        helpers._store = self._orig_helpers_store
        el._store = self._orig_el_store
        el._lan_address = self._orig_lan
        self.tmp.cleanup()

    # -- helpers ----------------------------------------------------

    def _scan(self):
        out = el.ebook_library(action="scan", path=str(self.book_dir))
        self.assertIn("2 book file(s) read", out, out)
        return out

    def _http(self, method, path, body=None, auth=True, raw_headers=None):
        url = el._SERVER.base_url() + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if auth:
            token = base64.b64encode(
                f"vyuk:{el._SERVER.token}".encode()).decode()
            req.add_header("Authorization", f"Basic {token}")
        if raw_headers:
            for key, value in raw_headers.items():
                req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def _serve(self):
        out = el.ebook_library(action="serve")
        self.assertIn("Ebook server running at", out, out)
        self._serving = True
        return out

    # -- KOReader hash ----------------------------------------------

    def test_koreader_hash_is_stable_hex(self):
        first = el.koreader_hash(self.epub)
        second = el.koreader_hash(self.epub)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 32)
        int(first, 16)

    def test_koreader_hash_head_change_changes_it(self):
        before = el.koreader_hash(self.epub)
        data = bytearray(self.epub.read_bytes())
        data[0] ^= 0xFF
        changed = self.root / "changed.epub"
        changed.write_bytes(bytes(data))
        self.assertNotEqual(el.koreader_hash(changed), before)

    def test_koreader_hash_append_at_end_does_not_change_it(self):
        # Appending a few bytes at the end crosses no sample offset
        # (the next sample after 1024 is 4096), so the digest is
        # unchanged — the Lua comment's whole point.
        before = el.koreader_hash(self.epub)
        appended = self.root / "appended.epub"
        appended.write_bytes(self.epub.read_bytes() + b"X" * 10)
        self.assertEqual(el.koreader_hash(appended), before)

    def test_koreader_hash_matches_manual_offsets(self):
        offsets = [0, 1024, 4096, 16384, 65536, 262144, 1048576,
                   4194304, 16777216, 67108864, 268435456, 1073741824]
        digest = hashlib.md5()
        with open(self.epub, "rb") as fh:
            for offset in offsets:
                fh.seek(offset)
                chunk = fh.read(1024)
                if chunk:
                    digest.update(chunk)
        self.assertEqual(el.koreader_hash(self.epub), digest.hexdigest())

    # -- metadata readers -------------------------------------------

    def test_epub_metadata(self):
        meta, cover, err = el._read_epub(str(self.epub))
        self.assertEqual(err, "")
        self.assertEqual(meta["title"], "Test Book")
        self.assertEqual(meta["author"], "Test Author")
        self.assertEqual(meta["series"], "Test Series")
        self.assertEqual(meta["series_index"], "2")
        self.assertEqual(meta["pages"], 3)
        self.assertEqual(len(meta["spine"]), 3)
        self.assertIsNotNone(cover)
        self.assertTrue(cover.startswith(b"\xff\xd8"))

    def test_cbz_metadata_with_comicinfo(self):
        meta, cover, err = el._read_cbz(str(self.cbz))
        self.assertEqual(err, "")
        self.assertEqual(meta["title"], "Test Comic")
        self.assertEqual(meta["series"], "Test Series")
        self.assertEqual(meta["author"], "Comic Author")
        self.assertEqual(meta["pages"], 5)
        self.assertIsNotNone(cover)

    def test_cbz_filename_title_without_comicinfo(self):
        plain = self.book_dir / "lone-comic.cbz"
        _make_cbz(plain, with_comicinfo=False)
        meta, _cover, err = el._read_cbz(str(plain))
        self.assertEqual(err, "")
        self.assertEqual(meta["title"], "lone-comic")
        self.assertEqual(meta["pages"], 5)

    # -- library actions --------------------------------------------

    def test_scan_counts_skips_and_no_dupes(self):
        out = self._scan()
        self.assertIn("2 new", out, out)
        self.assertIn("1 CBR (RAR, not readable in stdlib)", out, out)
        self.assertIn("1 PDF (page count not read in this build)", out, out)
        again = el.ebook_library(action="scan", path=str(self.book_dir))
        self.assertIn("0 new", again, again)
        self.assertIn("2 updated", again, again)
        self.assertIn("Library now holds 2 book(s).", again, again)

    def test_scan_missing_folder_refused(self):
        out = el.ebook_library(action="scan", path=str(self.root / "nope"))
        self.assertIn("No such folder", out)

    def test_list_and_search(self):
        self._scan()
        listing = el.ebook_library(action="list")
        self.assertIn("Test Book", listing)
        self.assertIn("Test Comic", listing)
        self.assertIn("3 documents", listing)
        self.assertIn("5 pages", listing)
        hits = el.ebook_library(action="search", book="comic")
        self.assertIn("Test Comic", hits)
        self.assertNotIn("Test Book", hits)
        misses = el.ebook_library(action="search", book="zzz-no-such")
        self.assertIn("No books match", misses)

    def test_progress_set_get_and_derivation(self):
        self._scan()
        out = el.ebook_library(action="progress", book="Test Book",
                               percentage=50)
        self.assertIn("50% read", out, out)
        got = el.ebook_library(action="progress", book="Test Book")
        self.assertIn("50% read", got, got)
        out = el.ebook_library(action="progress", book="Test Comic",
                               page=3)
        self.assertIn("60% read", out, out)
        self.assertIn("page 3", out, out)

    def test_progress_invalid_percentage_refused(self):
        self._scan()
        out = el.ebook_library(action="progress", book="Test Book",
                               percentage=150)
        self.assertIn("between 0 and 100", out)

    def test_progress_unknown_book_refused(self):
        out = el.ebook_library(action="progress", book="No Such Book",
                               percentage=10)
        self.assertIn("No book named", out)

    def test_remove_forgets_but_keeps_file(self):
        self._scan()
        out = el.ebook_library(action="remove", book="Test Book")
        self.assertIn("Forgot 'Test Book'", out, out)
        self.assertTrue(self.epub.is_file())
        listing = el.ebook_library(action="list")
        self.assertNotIn("Test Book", listing)
        self.assertIn("Test Comic", listing)

    # -- server: OPDS -----------------------------------------------

    def test_serve_requires_auth(self):
        self._scan()
        self._serve()
        status, _headers, _body = self._http(
            "GET", "/opds/v1.2/catalog", auth=False)
        self.assertEqual(status, 401)

    def test_opds_catalog(self):
        self._scan()
        self._serve()
        status, _headers, body = self._http("GET", "/opds/v1.2/catalog")
        self.assertEqual(status, 200)
        self.assertIn(b"All books", body)
        self.assertIn(b"Series", body)
        self.assertIn(b"Search", body)
        self.assertIn(b"<feed", body)

    def test_opds_books_and_download(self):
        self._scan()
        self._serve()
        status, _headers, body = self._http("GET", "/opds/v1.2/books")
        self.assertEqual(status, 200)
        self.assertIn(b"Test Book", body)
        self.assertIn(b"Test Comic", body)
        self.assertIn(b"opds-spec.org/acquisition", body)
        entries, _broken = el._load_books()
        book = next(e for e in entries if e["title"] == "Test Book")
        status, headers, data = self._http(
            "GET", f"/opds/v1.2/books/{book['hash']}/file")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "application/epub+zip")
        self.assertEqual(data, self.epub.read_bytes())
        status, _headers, _body = self._http(
            "GET", "/opds/v1.2/books/no-such-hash/file")
        self.assertEqual(status, 404)

    def test_opds_series_and_search(self):
        self._scan()
        self._serve()
        status, _headers, body = self._http("GET", "/opds/v1.2/series")
        self.assertEqual(status, 200)
        self.assertIn(b"Test Series", body)
        status, _headers, body = self._http(
            "GET", "/opds/v1.2/search?q=comic")
        self.assertEqual(status, 200)
        self.assertIn(b"Test Comic", body)
        self.assertNotIn(b"Test Book", body)
        status, _headers, body = self._http("GET", "/opds/v1.2/search")
        self.assertEqual(status, 200)
        self.assertIn(b"OpenSearchDescription", body)

    def test_opds_cover(self):
        self._scan()
        self._serve()
        entries, _broken = el._load_books()
        book = next(e for e in entries if e["title"] == "Test Book")
        status, headers, data = self._http(
            "GET", f"/opds/v1.2/books/{book['hash']}/cover")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/jpeg")
        self.assertTrue(data.startswith(b"\xff\xd8"))

    # -- server: KOReader Sync --------------------------------------

    def test_koreader_auth_and_create(self):
        self._scan()
        self._serve()
        status, _headers, body = self._http("GET", "/koreader/users/auth")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"authorized": "OK"})
        status, _headers, _body = self._http(
            "POST", "/koreader/users/create")
        self.assertEqual(status, 403)

    def test_koreader_progress_roundtrip(self):
        self._scan()
        self._serve()
        entries, _broken = el._load_books()
        book = next(e for e in entries if e["title"] == "Test Comic")
        book_hash = book["hash"]
        status, _headers, body = self._http(
            "GET", f"/koreader/syncs/progress/{book_hash}")
        self.assertEqual(status, 200)
        dto = json.loads(body)
        self.assertEqual(dto["document"], book_hash)
        self.assertEqual(dto["percentage"], 0.0)
        status, _headers, body = self._http(
            "PUT", "/koreader/syncs/progress",
            body={"document": book_hash, "percentage": 0.6,
                  "progress": "3", "device": "TestReader",
                  "device_id": "dev-1"})
        self.assertEqual(status, 200)
        status, _headers, body = self._http(
            "GET", f"/koreader/syncs/progress/{book_hash}")
        dto = json.loads(body)
        self.assertEqual(dto["percentage"], 0.6)
        self.assertEqual(dto["progress"], "3")
        self.assertEqual(dto["device"], "TestReader")
        self.assertEqual(dto["device_id"], "dev-1")
        status, _headers, _body = self._http(
            "GET", "/koreader/syncs/progress/" + "0" * 32)
        self.assertEqual(status, 404)
        status, _headers, _body = self._http(
            "PUT", "/koreader/syncs/progress",
            body={"document": "0" * 32, "percentage": 0.5,
                  "progress": "1", "device": "x", "device_id": "y"})
        self.assertEqual(status, 404)

    def test_koreader_epub_docfragment(self):
        self._scan()
        self._serve()
        entries, _broken = el._load_books()
        book = next(e for e in entries if e["title"] == "Test Book")
        book_hash = book["hash"]
        status, _headers, _body = self._http(
            "PUT", "/koreader/syncs/progress",
            body={"document": book_hash, "percentage": 0.5,
                  "progress": "/body/DocFragment[2].0",
                  "device": "TestReader", "device_id": "dev-1"})
        self.assertEqual(status, 200)
        status, _headers, body = self._http(
            "GET", f"/koreader/syncs/progress/{book_hash}")
        dto = json.loads(body)
        self.assertEqual(dto["progress"], "/body/DocFragment[2].0")
        self.assertEqual(dto["percentage"], 0.5)
        got = el.ebook_library(action="progress", book="Test Book")
        self.assertIn("50% read", got)

    def test_stop_and_status(self):
        self._scan()
        before = el.ebook_library(action="status")
        self.assertIn("not running", before)
        self._serve()
        during = el.ebook_library(action="status")
        self.assertIn("running at", during)
        self.assertIn("Library: 2 book(s)", during)
        out = el.ebook_library(action="stop")
        self.assertIn("stopped", out)
        after = el.ebook_library(action="status")
        self.assertIn("not running", after)

    # -- wiring -----------------------------------------------------

    def test_census(self):
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 772)  # INTENTIONAL — R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free-by-design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2126)  # INTENTIONAL — R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        snap = json.loads(Path(
            os.path.join(os.path.dirname(__file__),
                         "_classification_snapshot.json")).read_text())
        self.assertEqual(len(snap), 772)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(snap["ebook_library"],
                         {"confirm": False, "free": "ebook",
                          "read": False})

    def test_gate_free_and_readclass(self):
        self.assertEqual(_FREE_BY_DESIGN["ebook_library"], "ebook")
        self.assertIn("ebook", el.__doc__)
        for action in ("list", "search", "status"):
            self.assertTrue(_is_read_action(
                "ebook_library", {"action": action}), action)
        for action in ("scan", "progress", "serve", "stop", "remove"):
            self.assertFalse(_is_read_action(
                "ebook_library", {"action": action}), action)


if __name__ == "__main__":
    unittest.main()
