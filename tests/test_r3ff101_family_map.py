"""F-F101: the derivation family map must cover every registered tool.

The map (r3f10_hunt49_work/family_map.json) is derived from this
tree's registry by r3f10_hunt49_work/family_map.py, and the census
(r3f_permstools_work/r3f_census.json) is derived from the map by
r3f_permstools_work/census_derive.py. The map was built before the Q6
tool landed and no derivation rule covered its handler module
(core.morning_briefing), so get_morning_briefing was absent from the
map and read family "???" in the census while the audit doc already
counted comms_calendar 28.

Lab-inventory guard: the map and census are code-lab artefacts that
describe this tree's registry, not shipped files. The tests read them
by path (overridable via VYUK_FAMILY_MAP_JSON / VYUK_CENSUS_JSON --
the prescratch fail-first run points them at the pre-fix copies) and
SKIP when the files are absent, so the shipped tree never fails for
want of a lab file. The audit doc (TOOLS_LOOPHOLES_AUDIT.md) lives in
this tree and is always read.
"""
import json
import os
import re
import unittest

from core.funnel.tools_registry import tools

MAP_PATH = os.environ.get(
    "VYUK_FAMILY_MAP_JSON",
    "/home/hatch/workspace/code-lab/r3f10_hunt49_work/family_map.json")
CENSUS_PATH = os.environ.get(
    "VYUK_CENSUS_JSON",
    "/home/hatch/workspace/code-lab/r3f_permstools_work/r3f_census.json")

KNOWN_FAMILIES = {
    "browser_web", "capture_vision", "clipboard_io", "comms_calendar",
    "creative_image", "desktop_control", "diagnostics_dev", "files",
    "info_lookup", "media_audio", "productivity", "system_apps",
    "travel", "utilities",
}


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _audit_counts():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    doc = open(os.path.join(root, "TOOLS_LOOPHOLES_AUDIT.md"),
               encoding="utf-8").read()
    return json.loads(
        re.search(r"<!-- AUDIT-COUNTS: (\{.*?\}) -->", doc).group(1))


def _artefacts_present():
    return os.path.exists(MAP_PATH) and os.path.exists(CENSUS_PATH)


@unittest.skipUnless(_artefacts_present(),
                     "family-map artefacts absent (lab-inventory guard)")
class TestFamilyMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = sorted(d["name"] for d in tools.get_declarations())
        cls.fam_map = _load_json(MAP_PATH)
        cls.census = _load_json(CENSUS_PATH)
        cls.cen_by_name = {e["name"]: e for e in cls.census}

    def test_get_morning_briefing_is_comms_calendar(self):
        # F-F101: the filed tool reads its real family in both artefacts.
        self.assertEqual(self.fam_map.get("get_morning_briefing"),
                         "comms_calendar")
        self.assertEqual(
            self.cen_by_name["get_morning_briefing"]["family"],
            "comms_calendar")

    def test_no_tool_unmapped(self):
        # Whole-map pin: every registered tool has exactly one known
        # family in the map -- no absent key, no "???" placeholder.
        missing = [n for n in self.names if n not in self.fam_map]
        self.assertEqual(missing, [])
        unknown = {n: f for n, f in self.fam_map.items()
                   if f not in KNOWN_FAMILIES}
        self.assertEqual(unknown, {})
        self.assertEqual(set(self.fam_map), set(self.names))

    def test_family_totals_match_audit_counts(self):
        # The map's per-family totals are the audit doc's AUDIT-COUNTS.
        from collections import Counter
        totals = dict(sorted(Counter(self.fam_map.values()).items()))
        self.assertEqual(totals, _audit_counts())

    def test_census_agrees_with_map(self):
        # The census family column is the map, entry for entry.
        self.assertEqual(
            [e["name"] for e in self.census if e.get("family") == "???"],
            [])
        disagree = {e["name"]: (e.get("family"),
                                self.fam_map.get(e["name"]))
                    for e in self.census
                    if self.fam_map.get(e["name"]) != e.get("family")}
        self.assertEqual(disagree, {})

    def test_census_totals(self):
        # The fix moves one family cell; the census totals do not move.
        read_n = sum(1 for e in self.census if e.get("read"))
        self.assertEqual(len(self.census), 764)  # INTENTIONAL; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(read_n, 384)  # INTENTIONAL; Stage 0 features batch 2 (Group E, 2026-11-22): 383 -> 384 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added
        self.assertEqual(len(self.census) - read_n, 380)  # INTENTIONAL; Stage 0 features batch 2 (Group E, 2026-11-22): 375 -> 378 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(
            sum(1 for e in self.census if e.get("confirm_gated")), 169)  # Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169

    def test_sibling_still_comms_calendar(self):
        # Control: the fix moves one tool; its calendar siblings stay.
        self.assertEqual(self.fam_map.get("get_todays_schedule"),
                         "comms_calendar")
        self.assertEqual(
            self.cen_by_name["get_todays_schedule"]["family"],
            "comms_calendar")


if __name__ == "__main__":
    unittest.main()
