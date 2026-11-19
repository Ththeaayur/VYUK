"""R17-9 wardrobe rebuild — the owner's own virtual closet. Fake
garments and fake outfits only: a scratch SQLite file per test, no
real clothes, no photos read, no network, no AI visualisation.
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".pkgroot"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import wardrobe as wd
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate_free import _FREE_BY_DESIGN
from core.funnel.tools_gate_readclass import _is_read_action
from core.funnel.tools_registry import tools

TREE = Path(__file__).resolve().parent.parent


def _iso(day):
    return day.isoformat()


class WardrobeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "wardrobe.sqlite3")
        # A real temp file for the photo-path rule (recorded, never opened).
        self.photo = os.path.join(self.tmp.name, "shirt.jpg")
        Path(self.photo).write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

    def tearDown(self):
        self.tmp.cleanup()

    # -- helpers ------------------------------------------------------

    def _garment(self, name="Navy shirt", category="top", colour="navy",
                 seasons="", warmth=2, formality=3, photo=None):
        return wd.wardrobe(
            "garment", mode="add", name=name, category=category,
            colour=colour, seasons=seasons, warmth=warmth,
            formality=formality, photo=photo if photo is not None else self.photo,
            _db_path=self.db)

    def _outfit(self, name="Work", ids="1;2;3"):
        return wd.wardrobe(
            "outfit", mode="build", name=name, garment_ids=ids,
            _db_path=self.db)

    def _closet(self):
        self.assertIn("Garment #1 added", self._garment())
        self.assertIn("Garment #2 added", self._garment(
            "Blue jeans", "bottom", "blue", warmth=2, formality=2))
        self.assertIn("Garment #3 added", self._garment(
            "White sneakers", "shoes", "white", warmth=2, formality=1))

    # -- garments -----------------------------------------------------

    def test_add_and_list_garment(self):
        self.assertIn("Garment #1 added", self._garment())
        out = wd.wardrobe("garment", mode="list", _db_path=self.db)
        self.assertIn("#1 Navy shirt (top, navy, all seasons, warmth 2/5, formality 3/5", out)
        self.assertIn("never worn", out)

    def test_add_refuses_bad_category_season_warmth_photo(self):
        self.assertIn("category must be one of", self._garment(category="hat"))
        self.assertIn("unknown season", self._garment(seasons="monsoon"))
        self.assertIn("warmth must be 1..5", self._garment(warmth=9))
        self.assertIn("photo file does not exist",
                      self._garment(photo="/no/such/file.jpg"))
        self.assertIn("needs a name", wd.wardrobe(
            "garment", mode="add", category="top", _db_path=self.db))

    def test_remove_garment_refused_when_in_outfit(self):
        self._closet()
        self.assertIn("Outfit #1 built", self._outfit())
        self.assertIn("is in outfit 1", wd.wardrobe(
            "garment", mode="remove", garment_id=1, _db_path=self.db))
        self.assertIn("Garment #", self._garment("Red scarf", "accessory", "red"))
        self.assertIn("Garment #4 removed.", wd.wardrobe(
            "garment", mode="remove", garment_id=4, _db_path=self.db))
        self.assertIn("no garment 99", wd.wardrobe(
            "garment", mode="remove", garment_id=99, _db_path=self.db))

    def test_list_filters_by_category_and_season(self):
        self._closet()
        self._garment("Wool coat", "outerwear", "grey", seasons="winter", warmth=5)
        out = wd.wardrobe("garment", mode="list", category="outerwear", _db_path=self.db)
        self.assertIn("Wool coat", out)
        self.assertNotIn("Navy shirt", out)
        # A winter date lists the coat and the all-season garments, not a summer-only one.
        self._garment("Linen shorts", "bottom", "beige", seasons="summer", warmth=1)
        out = wd.wardrobe("garment", mode="list", date="2026-01-15", _db_path=self.db)
        self.assertIn("Wool coat", out)
        self.assertIn("Navy shirt", out)
        self.assertNotIn("Linen shorts", out)

    # -- outfits ------------------------------------------------------

    def test_build_complete_outfit(self):
        self._closet()
        out = self._outfit()
        self.assertIn("Outfit #1 built", out)
        self.assertIn("complete", out)
        self.assertNotIn("missing", out)

    def test_build_dress_outfit_complete(self):
        self.assertIn("Garment #1 added", self._garment(
            "Black dress", "dress", "black", warmth=2, formality=4))
        self.assertIn("Garment #2 added", self._garment(
            "Heels", "shoes", "black", warmth=1, formality=5))
        out = wd.wardrobe("outfit", mode="build", name="Evening",
                          garment_ids="1;2", _db_path=self.db)
        self.assertIn("complete", out)

    def test_build_refuses_conflicts_and_unknown(self):
        self._closet()
        self._garment("White tee", "top", "white")
        self.assertIn("two tops", wd.wardrobe(
            "outfit", mode="build", name="Two tops", garment_ids="1;4;2;3",
            _db_path=self.db))
        self._garment("Sundress", "dress", "yellow")
        self.assertIn("dress is the top and the bottom", wd.wardrobe(
            "outfit", mode="build", name="Dress plus top", garment_ids="1;5;3",
            _db_path=self.db))
        self.assertIn("no garment 99", wd.wardrobe(
            "outfit", mode="build", name="Ghost", garment_ids="1;99",
            _db_path=self.db))
        self.assertIn("needs garment_ids", wd.wardrobe(
            "outfit", mode="build", name="Empty", garment_ids="",
            _db_path=self.db))

    def test_build_warns_when_incomplete(self):
        self._closet()
        out = wd.wardrobe("outfit", mode="build", name="No shoes",
                          garment_ids="1;2", _db_path=self.db)
        self.assertIn("Outfit #1 built", out)
        self.assertIn("missing: shoes", out)

    def test_remove_outfit_refused_when_scheduled(self):
        self._closet()
        self.assertIn("Outfit #1 built", self._outfit())
        future = _iso(date.today() + timedelta(days=3))
        self.assertIn(f"Scheduled outfit #1 (Work) for {future}",
                      wd.wardrobe("schedule", mode="add", outfit_id=1,
                                  date=future, _db_path=self.db))
        self.assertIn("is scheduled for", wd.wardrobe(
            "outfit", mode="remove", outfit_id=1, _db_path=self.db))
        self.assertIn("Unscheduled entry #1.", wd.wardrobe(
            "schedule", mode="remove", schedule_id=1, _db_path=self.db))
        self.assertIn("Outfit #1 removed.", wd.wardrobe(
            "outfit", mode="remove", outfit_id=1, _db_path=self.db))

    # -- suggest ------------------------------------------------------

    def test_suggest_picks_by_temperature(self):
        self._closet()
        self._garment("Wool coat", "outerwear", "grey", seasons="winter", warmth=5)
        self._garment("Linen shirt", "top", "white", seasons="summer", warmth=1)
        hot = wd.wardrobe("suggest", date="2026-07-15", temp_c=31, _db_path=self.db)
        self.assertIn("Linen shirt", hot)
        self.assertNotIn("Wool coat", hot)
        cold = wd.wardrobe("suggest", date="2026-01-15", temp_c=4, _db_path=self.db)
        self.assertIn("Wool coat", cold)
        self.assertNotIn("Linen shirt", cold)

    def test_suggest_empty_wardrobe_is_honest(self):
        out = wd.wardrobe("suggest", date="2026-07-15", temp_c=30, _db_path=self.db)
        self.assertIn("Wardrobe is empty", out)
        self.assertIn("add garments first", out)

    def test_suggest_respects_formality_and_rain(self):
        self._closet()
        self._garment("Blazer", "outerwear", "black", warmth=3, formality=5)
        out = wd.wardrobe("suggest", date="2026-07-15", temp_c=18,
                          condition="rain", formality=5, _db_path=self.db)
        self.assertIn("Blazer", out)
        self.assertNotIn("White sneakers", out)
        plain = wd.wardrobe("suggest", date="2026-07-15", temp_c=18,
                            condition="rain", _db_path=self.db)
        self.assertIn("Blazer", plain)

    def test_suggest_prefers_least_recently_worn(self):
        self._closet()
        self._garment("Grey tee", "top", "grey", warmth=2, formality=2)
        self.assertIn("Outfit #1 built", self._outfit())
        yesterday = _iso(date.today() - timedelta(days=1))
        self.assertIn(f"worn on {yesterday}", wd.wardrobe(
            "wear", outfit_id=1, date=yesterday, _db_path=self.db))
        out = wd.wardrobe("suggest", date=_iso(date.today()), temp_c=24, _db_path=self.db)
        self.assertIn("Grey tee", out)
        self.assertNotIn("Navy shirt", out)

    # -- schedule + wear ----------------------------------------------

    def test_schedule_duplicate_and_past_refused(self):
        self._closet()
        self.assertIn("Outfit #1 built", self._outfit())
        future = _iso(date.today() + timedelta(days=2))
        self.assertIn(f"Scheduled outfit #1 (Work) for {future}",
                      wd.wardrobe("schedule", mode="add", outfit_id=1,
                                  date=future, _db_path=self.db))
        self.assertIn("already has an outfit scheduled", wd.wardrobe(
            "schedule", mode="add", outfit_id=1, date=future, _db_path=self.db))
        self.assertIn("cannot schedule the past", wd.wardrobe(
            "schedule", mode="add", outfit_id=1, date="2020-01-01",
            _db_path=self.db))
        self.assertIn(future, wd.wardrobe("schedule", mode="list", _db_path=self.db))

    def test_wear_marks_and_updates_last_worn(self):
        self._closet()
        self.assertIn("Outfit #1 built", self._outfit())
        today = _iso(date.today())
        self.assertIn(f"worn on {today}", wd.wardrobe(
            "wear", outfit_id=1, date=today, _db_path=self.db))
        self.assertIn("already marked worn", wd.wardrobe(
            "wear", outfit_id=1, date=today, _db_path=self.db))
        self.assertIn("cannot mark a future date", wd.wardrobe(
            "wear", outfit_id=1, date=_iso(date.today() + timedelta(days=1)),
            _db_path=self.db))
        out = wd.wardrobe("garment", mode="list", _db_path=self.db)
        self.assertIn(f"last worn {today}", out)

    def test_status_counts(self):
        self._closet()
        out = wd.wardrobe("status", _db_path=self.db)
        self.assertIn("3 garments, 0 outfits, 0 scheduled", out)
        self.assertIn("top 1, bottom 1, shoes 1", out)
        self.assertIn("never worn: 3", out)

    # -- events -------------------------------------------------------

    def test_events_chain_verifies(self):
        self._closet()
        self.assertIn("Outfit #1 built", self._outfit())
        out = wd.wardrobe("events", _db_path=self.db)
        self.assertIn("chain OK", out)
        self.assertIn("garment_added", out)
        self.assertIn("outfit_built", out)
        db = wd._WardrobeDB(self.db)
        ok, checked, bad = db.verify_chain()
        self.assertTrue(ok)
        self.assertEqual(checked, 4)
        self.assertIsNone(bad)

    def test_events_chain_break_is_detected(self):
        self._closet()
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE events SET payload = 'tampered' WHERE id = 1")
        conn.commit()
        conn.close()
        db = wd._WardrobeDB(self.db)
        ok, checked, bad = db.verify_chain()
        self.assertFalse(ok)
        self.assertEqual(bad, 1)

    # -- wiring + census ----------------------------------------------

    def test_registry_and_classification(self):
        decls = tools.get_declarations()
        names = [d["name"] for d in decls]
        self.assertIn("wardrobe", names)
        self.assertEqual(len(decls), 758)  # INTENTIONAL — R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        snap = json.load(open(TREE / "tests" / "_classification_snapshot.json"))
        self.assertEqual(snap["wardrobe"],
                         {"confirm": False, "free": "app-state", "read": False})
        self.assertEqual(_FREE_BY_DESIGN.get("wardrobe"), "app-state")
        # List/status/events/suggest are reads; adds/builds/schedules/wear are not.
        self.assertTrue(_is_read_action("wardrobe", {"action": "garment", "mode": "list"}))
        self.assertTrue(_is_read_action("wardrobe", {"action": "suggest"}))
        self.assertTrue(_is_read_action("wardrobe", {"action": "status"}))
        self.assertTrue(_is_read_action("wardrobe", {"action": "events"}))
        self.assertFalse(_is_read_action("wardrobe", {"action": "garment", "mode": "add"}))
        self.assertFalse(_is_read_action("wardrobe", {"action": "outfit", "mode": "build"}))
        self.assertFalse(_is_read_action("wardrobe", {"action": "wear"}))

    def test_catalogues_list_wardrobe_in_your_world(self):
        self.assertIn("wardrobe", GROUP_MEMBERS["Your world"])
        text = (TREE / "core" / "catalogue_groups_data.py").read_text()
        lines = text.splitlines()
        solar_at = next(i for i, line in enumerate(lines)
                        if line.strip() == '"solar_yield",')
        wardrobe_at = next(i for i, line in enumerate(lines)
                           if line.strip() == '"wardrobe",')
        self.assertEqual(wardrobe_at, solar_at + 1)

    def test_audit_pins(self):
        totals = json.loads(
            re.search(r"<!-- AUDIT-TOTALS: (\{.*?\}) -->",
                      (TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text()).group(1))
        self.assertEqual(totals["total"], 758)  # INTENTIONAL — R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertEqual(totals["read"], 383)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 382 -> 383 — one new read tool get_morning_briefing
        self.assertEqual(totals["effectful"], 375)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 353 -> 354; R18-4 a2a pack (2026-11-12): 354 -> 355; R18-5 dspy pack (2026-11-12): 355 -> 356; R18-6 fonoster pack (2026-11-12): 356 -> 357; R18-7 attendize pack (2026-11-12): 357 -> 358; R18-8 ta pack (2026-11-12): 358 -> 359; R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374 -> 375 — one new tool run_plan
        self.assertEqual(totals["confirm_gated"], 166)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 154 -> 155; R18-4 a2a pack (2026-11-12): 155 -> 156; R18-5 dspy pack (2026-11-12): 156 -> 157; R18-6 fonoster pack (2026-11-12): 157 -> 158; R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling
        self.assertEqual(totals["already_verified_in_handler"], 309)  # INTENTIONAL — R18-3 mcp-context-forge pack (2026-11-12): 287 -> 288; R18-4 a2a pack (2026-11-12): 288 -> 289; R18-5 dspy pack (2026-11-12): 289 -> 290; R18-6 fonoster pack (2026-11-12): 290 -> 291; R18-7 attendize pack (2026-11-12): 291 -> 292; R18-8 ta pack (2026-11-12): 292 -> 293; R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan
        counts = json.loads(
            re.search(r"<!-- AUDIT-COUNTS: (\{.*?\}) -->",
                      (TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text()).group(1))
        self.assertEqual(counts["productivity"], 146)  # INTENTIONAL — R18-7 attendize pack (2026-11-12): productivity 133 -> 134 (effectful 75 -> 76); R18-8 ta pack (2026-11-12): productivity 134 -> 135 (effectful 76 -> 77); R18-10 timetrace pack (2026-11-12): productivity 135 -> 136 (effectful 77 -> 78); R19-2 ortools pack (2026-11-12): productivity 136 -> 137 (effectful 78 -> 79); R19-3 aureuserp pack (2026-11-12): productivity 137 -> 138 (effectful 79 -> 80); R19-5 invoify pack (2026-11-12): productivity 138 -> 139 (effectful 80 -> 81); R19-6 tolgee pack (2026-11-12): productivity 139 -> 140 (effectful 81 -> 82); R19-7 spoolman pack (2026-11-12): productivity 140 -> 141 (effectful 82 -> 83); R19-8 djangocrm pack (2026-11-12): productivity 141 -> 142 (effectful 83 -> 84); R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 -> 145 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 -> 146 — one new tool run_plan
        self.assertEqual(sum(counts.values()), 758)  # INTENTIONAL — R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing

    def test_licence_marker_is_proposed(self):
        head = (TREE / "core" / "wardrobe.py").read_text().split('"""')[1]
        # R18-1 neighbour fix: the parent ruled this licence in R17-9
        # verification ("Parent-ruled (licence, 2026-11-12)"), so the
        # marker no longer reads PROPOSED — the test asserts the ruled
        # state, not the pre-ruling one.
        self.assertIn("Parent-ruled (licence, 2026-11-12)", head)
        self.assertIn("tandpfun/wardrobe", head)
        self.assertIn("INSPIRATION ONLY", head)


if __name__ == "__main__":
    unittest.main()
