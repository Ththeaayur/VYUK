"""R9-8 elodie port — EXIF photo organiser (own code, no adoption).

Synthetic JPEGs with real EXIF (Pillow, plain-dict sub-IFDs — the
Exif-object assignment path fails Pillow's tobytes() on both lab
interpreters, a Pillow quirk, not a tree defect). Hermetic: temp
dirs only, no operator artifact, no skips.
"""

import hashlib
import os
import pathlib
import shutil
import tempfile
import unittest
from datetime import datetime

from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from core import photo_organise as po
from core.funnel.tools_registry import tools as _registry

ROOT = pathlib.Path(po.__file__).resolve().parent.parent


def _make_jpeg(path, date=None, gps=None, size=(8, 8), date_original=None):
    """A small JPEG, optionally with EXIF DateTimeOriginal (36867)
    and GPS (lat/lon as decimal degrees, N/E positive). `date` sets
    both 306 and 36867; `date_original` overrides 36867 alone, so a
    test can pin the precedence (original wins over 306)."""
    img = Image.new("RGB", size, "red")
    exif = Image.Exif()
    if date is not None:
        exif[306] = date
        exif[0x8769] = {36867: date_original or date}
    if gps is not None:
        lat, lon = gps
        lat_ref = "N" if lat >= 0 else "S"
        lon_ref = "E" if lon >= 0 else "W"
        lat = abs(lat)
        lon = abs(lon)
        lat_d, lat_m = int(lat), int((lat - int(lat)) * 60)
        lat_s = (lat - int(lat) - lat_m / 60.0) * 3600
        lon_d, lon_m = int(lon), int((lon - int(lon)) * 60)
        lon_s = (lon - int(lon) - lon_m / 60.0) * 3600
        exif[0x8825] = {
            1: lat_ref,
            2: (IFDRational(lat_d), IFDRational(lat_m),
                IFDRational(int(round(lat_s * 100)), 100)),
            3: lon_ref,
            4: (IFDRational(lon_d), IFDRational(lon_m),
                IFDRational(int(round(lon_s * 100)), 100)),
        }
    img.save(path, exif=exif)
    return path


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()


class OrganiseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.src = pathlib.Path(self.tmp) / "camera-dump"
        self.dst = pathlib.Path(self.tmp) / "sorted"
        self.src.mkdir()
        self.dst.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_date_from_exif_original(self):
        # 306 (last-modified) says 12:00, DateTimeOriginal says
        # 12:34 — the organiser sorts by the shutter moment.
        _make_jpeg(self.src / "dsc_1234.jpg", date="2015:01:01 12:00:00",
                   date_original="2015:01:01 12:34:56")
        text = po.organise_photos(str(self.src), str(self.dst),
                                  dry_run=False)
        target = (self.dst / "2015-01-Jan" / "Unknown Location"
                  / "2015-01-01_12-34-56-dsc_1234.jpg")
        self.assertTrue(target.exists(), text)
        self.assertFalse((self.src / "dsc_1234.jpg").exists())
        self.assertIn("Moved 1 photo(s)", text)

    def test_dry_run_changes_nothing(self):
        _make_jpeg(self.src / "dsc_1234.jpg", date="2015:01:01 12:34:56")
        before = sorted(p.name for p in self.src.rglob("*"))
        text = po.organise_photos(str(self.src), str(self.dst),
                                  dry_run=True)
        self.assertIn("Dry run", text)
        self.assertIn("2015-01-01_12-34-56-dsc_1234.jpg", text)
        self.assertEqual(sorted(p.name for p in self.src.rglob("*")),
                         before)
        self.assertEqual(list(self.dst.rglob("*")), [])

    def test_preview_is_the_same_plan(self):
        _make_jpeg(self.src / "dsc_1234.jpg", date="2015:01:01 12:34:56")
        plan = po.preview_photo_organisation(str(self.src), str(self.dst))
        self.assertIn("2015-01-01_12-34-56-dsc_1234.jpg", plan)
        self.assertEqual(list(self.dst.rglob("*")), [])
        self.assertTrue((self.src / "dsc_1234.jpg").exists())

    def test_mtime_fallback_when_no_exif(self):
        path = _make_jpeg(self.src / "plain.jpg")
        stamp = datetime(2020, 6, 15, 8, 0, 0).timestamp()
        os.utime(path, (stamp, stamp))
        text = po.organise_photos(str(self.src), str(self.dst),
                                  dry_run=False)
        target = (self.dst / "2020-06-Jun" / "Unknown Location"
                  / "2020-06-15_08-00-00-plain.jpg")
        self.assertTrue(target.exists(), text)

    def test_gps_coarse_bucket(self):
        # 19 deg 7' N, 72 deg 52' E -> 19.1N, 72.9E (0.1-degree bucket).
        _make_jpeg(self.src / "goa.jpg", date="2015:01:01 12:34:56",
                   gps=(19.1167, 72.8667))
        text = po.organise_photos(str(self.src), str(self.dst),
                                  dry_run=False)
        target = (self.dst / "2015-01-Jan" / "GPS-19.1N-72.9E"
                  / "2015-01-01_12-34-56-goa.jpg")
        self.assertTrue(target.exists(), text)

    def test_gps_southern_western(self):
        _make_jpeg(self.src / "syd.jpg", date="2015:01:01 12:34:56",
                   gps=(-33.86, -70.66))
        po.organise_photos(str(self.src), str(self.dst), dry_run=False)
        target = (self.dst / "2015-01-Jan" / "GPS-33.9S-70.7W"
                  / "2015-01-01_12-34-56-syd.jpg")
        self.assertTrue(target.exists())

    def test_no_gps_goes_to_unknown_location(self):
        _make_jpeg(self.src / "nogps.jpg", date="2015:01:01 12:34:56")
        po.organise_photos(str(self.src), str(self.dst), dry_run=False)
        self.assertTrue(
            (self.dst / "2015-01-Jan" / "Unknown Location"
             / "2015-01-01_12-34-56-nogps.jpg").exists())

    def test_move_verified_by_bytes(self):
        path = _make_jpeg(self.src / "dsc_1.jpg",
                          date="2015:01:01 12:34:56")
        digest = _sha256(path)
        po.organise_photos(str(self.src), str(self.dst), dry_run=False)
        target = (self.dst / "2015-01-Jan" / "Unknown Location"
                  / "2015-01-01_12-34-56-dsc_1.jpg")
        self.assertEqual(_sha256(target), digest)

    def test_non_clobbering_suffix(self):
        _make_jpeg(self.src / "dsc_1.jpg", date="2015:01:01 12:34:56")
        taken = (self.dst / "2015-01-Jan" / "Unknown Location"
                 / "2015-01-01_12-34-56-dsc_1.jpg")
        taken.parent.mkdir(parents=True)
        taken.write_bytes(b"different bytes, same name")
        text = po.organise_photos(str(self.src), str(self.dst),
                                  dry_run=False)
        suffixed = (self.dst / "2015-01-Jan" / "Unknown Location"
                    / "2015-01-01_12-34-56-dsc_1 (2).jpg")
        self.assertTrue(suffixed.exists(), text)
        self.assertEqual(taken.read_bytes(), b"different bytes, same name")
        self.assertIn("already existed, so the suffixed name", text)

    def test_already_organised_identical_skip(self):
        path = _make_jpeg(self.src / "dsc_1.jpg",
                          date="2015:01:01 12:34:56")
        target = (self.dst / "2015-01-Jan" / "Unknown Location"
                  / "2015-01-01_12-34-56-dsc_1.jpg")
        target.parent.mkdir(parents=True)
        shutil.copyfile(path, target)
        text = po.organise_photos(str(self.src), str(self.dst),
                                  dry_run=False)
        self.assertIn("already sit at their organised destination", text)
        self.assertTrue((self.src / "dsc_1.jpg").exists())
        self.assertTrue(target.exists())

    def test_video_left_untouched(self):
        _make_jpeg(self.src / "dsc_1.jpg", date="2015:01:01 12:34:56")
        (self.src / "clip.mp4").write_bytes(b"fake video bytes")
        text = po.organise_photos(str(self.src), str(self.dst),
                                  dry_run=False)
        self.assertTrue((self.src / "clip.mp4").exists())
        self.assertIn("1 video file(s) left untouched", text)

    def test_unreadable_image_left_untouched(self):
        (self.src / "broken.jpg").write_bytes(b"not a jpeg at all")
        text = po.organise_photos(str(self.src), str(self.dst),
                                  dry_run=False)
        self.assertTrue((self.src / "broken.jpg").exists())
        self.assertIn("1 image file(s) couldn't be opened", text)

    def test_illegal_stem_sanitised(self):
        _make_jpeg(self.src / "my:photo?.jpg", date="2015:01:01 12:34:56")
        po.organise_photos(str(self.src), str(self.dst), dry_run=False)
        target = (self.dst / "2015-01-Jan" / "Unknown Location"
                  / "2015-01-01_12-34-56-my-photo-.jpg")
        self.assertTrue(target.exists())

    def test_refusals(self):
        self.assertIn("Folder not found",
                      po.organise_photos("/no/such/folder", str(self.dst)))
        self.assertIn("the same",
                      po.organise_photos(str(self.src), str(self.src)))
        self.assertIn("inside the source folder",
                      po.organise_photos(str(self.src),
                                         str(self.src / "sorted")))
        self.assertIn("Folder not found",
                      po.preview_photo_organisation("/no/such/folder",
                                                    str(self.dst)))

    def test_cancel_token_stops_the_run(self):
        _make_jpeg(self.src / "dsc_1.jpg", date="2015:01:01 12:34:56")
        _make_jpeg(self.src / "dsc_2.jpg", date="2015:01:02 12:34:56")
        text = po.organise_photos(str(self.src), str(self.dst),
                                  dry_run=False,
                                  cancel_token=lambda: True)
        self.assertIn("Moved 0 photo(s)", text)
        self.assertIn("cancelled", text)
        self.assertTrue((self.src / "dsc_1.jpg").exists())

    def test_destination_created_on_real_run_only(self):
        fresh = pathlib.Path(self.tmp) / "new-dest"
        _make_jpeg(self.src / "dsc_1.jpg", date="2015:01:01 12:34:56")
        po.organise_photos(str(self.src), str(fresh), dry_run=True)
        self.assertFalse(fresh.exists())
        po.organise_photos(str(self.src), str(fresh), dry_run=False)
        self.assertTrue(fresh.exists())


class UnitTests(unittest.TestCase):
    def test_read_photo_date_none_branch(self):
        class _BadImage:
            def getexif(self):
                raise ValueError("no exif here")

        taken, source = po._read_photo_date(
            _BadImage(), pathlib.Path("/no/such/file.jpg"))
        self.assertIsNone(taken)
        self.assertEqual(source, "none")

    def test_parse_exif_datetime(self):
        self.assertEqual(
            po._parse_exif_datetime("2015:01:01 12:34:56"),
            datetime(2015, 1, 1, 12, 34, 56))
        self.assertIsNone(po._parse_exif_datetime("not a date"))
        self.assertIsNone(po._parse_exif_datetime(None))

    def test_coarse_location(self):
        self.assertEqual(po._coarse_location(19.1167, 72.8667),
                         "GPS-19.1N-72.9E")
        self.assertEqual(po._coarse_location(-33.86, -70.66),
                         "GPS-33.9S-70.7W")
        self.assertEqual(po._coarse_location(0.0, 0.0), "GPS-0.0N-0.0E")


class RegistrationTests(unittest.TestCase):
    def test_tools_registered(self):
        names = {d["name"] for d in _registry.get_declarations()}
        self.assertIn("organise_photos", names)
        self.assertIn("preview_photo_organisation", names)

    def test_registry_count(self):
        # R9-8 elodie port: 704 -> 706 (+2).
        self.assertEqual(len(_registry.get_declarations()), 769)  # INTENTIONAL — R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_gates(self):
        from core.funnel.tools_gate import (
            _CONFIRM_GATE_TOOLS, _READ_ONLY_TOOLS)
        from core.funnel.tools_gate_egress import _LOCAL_ONLY_READS
        from core.funnel.tools_gate_taint import _UNTRUSTED_PRODUCERS
        from core.funnel.tools_handlers_limits import _TOOL_DEADLINE_CLASS

        self.assertIn("organise_photos", _CONFIRM_GATE_TOOLS)
        self.assertIn("preview_photo_organisation", _READ_ONLY_TOOLS)
        self.assertIn("preview_photo_organisation", _LOCAL_ONLY_READS)
        self.assertEqual(
            _UNTRUSTED_PRODUCERS.get("organise_photos"), "filenames")
        self.assertEqual(
            _UNTRUSTED_PRODUCERS.get("preview_photo_organisation"),
            "filenames")
        self.assertEqual(
            _TOOL_DEADLINE_CLASS.get("organise_photos"), "long")

    def test_no_elodie_anywhere(self):
        """PORT, not adopt: no elodie import, no elodie dependency,
        no elodie in the licence file. The module's own docstring
        names elodie in prose (the design source), so the docstring
        is stripped before scanning for import statements — the
        R8-4 precedent."""
        text = pathlib.Path(po.__file__).read_text(encoding="utf-8")
        body = text.split('"""', 2)[-1]
        self.assertNotIn("import elodie", body)
        self.assertNotIn("from elodie import", body)
        self.assertNotIn("subprocess", body)
        req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("elodie", req.lower())
        licences = (ROOT / "THIRD_PARTY_LICENSES.md").read_text(
            encoding="utf-8")
        self.assertNotIn("elodie", licences.lower())

    def test_catalogue_and_toolnames(self):
        # Read as text from the single catalogue source
        # (core/catalogue_groups_data.py, GAPS-23 F-V7) — the
        # names are checked against the file text instead
        # (the R8-4 precedent).
        catalog = (ROOT / "core" / "catalogue_groups_data.py").read_text(
            encoding="utf-8")
        self.assertIn('"organise_photos"', catalog)
        self.assertIn('"preview_photo_organisation"', catalog)
        names = (ROOT / "core" / "tool_labels.py").read_text(
            encoding="utf-8")
        self.assertIn('"organise_photos"', names)
        self.assertIn('"preview_photo_organisation"', names)


if __name__ == "__main__":
    unittest.main()
