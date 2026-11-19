"""R18-8 bukosabino/ta rebuild — technical analysis over
operator-supplied OHLCV candles (25 indicators, no live feed).

Every behavioural test drives a temp SQLite store (the module's
_get_store seam); nothing here touches the production store, the
network, or a live price feed. There is no feed to test: the
module makes no network call, and the mutual_fund.py no-fetch
boundary is untouched. The signals action reports mechanical
readings of the indicator values only — never advice — and
nothing here claims a recommendation.
"""
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".pkgroot"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import technical_analysis as ta
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
    _needs_confirmation,
)
from core.funnel.tools_registry import tools as _registry

TREE = Path(__file__).resolve().parent.parent


def _rising(n=40, start=100.0):
    return [{"open": start + i, "high": start + 0.5 + i,
             "low": start - 0.5 + i, "close": start + i,
             "volume": 1000} for i in range(n)]


def _mixed():
    # 40 closes that rise, fall, then rise — RSI lands mid-range.
    closes = [100, 102, 101, 103, 105, 104, 102, 100, 99, 101,
              103, 106, 108, 107, 105, 104, 106, 109, 111, 110,
              108, 107, 109, 112, 114, 113, 111, 110, 112, 115,
              117, 116, 114, 113, 115, 118, 120, 119, 117, 116]
    return [{"open": c - 0.2, "high": c + 0.5, "low": c - 0.5,
             "close": float(c), "volume": 1000} for c in closes]


class TaTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "ta.db")
        self._store_patch = mock.patch.object(
            ta, "_get_store", lambda: ta._TaStore(self.db))
        self._store_patch.start()
        self.addCleanup(self._store_patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.candles = _rising()
        self.candles_json = json.dumps(self.candles)

    def _add_dataset(self, name="Rising"):
        out = ta.technical_analysis(action="dataset", mode="add",
                                    name=name, candles=self.candles_json)
        self.assertIn("stored", out, out)
        return out


class TestRegistryCounts(TaTestCase):
    def test_registry_counts(self):
        decls = _registry.get_declarations()
        self.assertEqual(len(decls), 758, len(decls))  # INTENTIONAL — R18-8 ta pack (2026-11-12): 740 -> 741 — technical_analysis added; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in decls)
        self.assertEqual(total_params, 2079, total_params)  # INTENTIONAL — R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 755, grouped)  # INTENTIONAL — R18-8 ta pack (2026-11-12): 738 -> 739 — technical_analysis grouped in Your world; R18-10 timetrace pack (2026-11-12): 739 -> 740 — time_tracker grouped in Everyday skills; R19-1 videolingo pack (2026-11-12): 740 -> 741 — audio_localise grouped in Play & create; R19-2 ortools pack (2026-11-12): 741 -> 742 — optimise grouped in Everyday skills; R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing
        closed = sum(
            1 for d in decls
            if d.get("parameters", {}).get("additionalProperties") is False)
        self.assertEqual(closed, 757, closed)  # INTENTIONAL — R18-8 ta pack (2026-11-12): 739 -> 740 — technical_analysis added (additionalProperties False by the registry default, so the new tool is closed); R18-10 timetrace pack (2026-11-12): 740 -> 741 — time_tracker added (additionalProperties False by the registry default, so the new tool is closed); R19-1 videolingo pack (2026-11-12): 741 -> 742 — audio_localise added (additionalProperties False by the registry default, so the new tool is closed); R19-2 ortools pack (2026-11-12): 742 -> 743 — optimise added (additionalProperties False by the registry default, so the new tool is closed); R19-3 aureuserp pack (2026-11-12): 743 -> 744 — erp added (additionalProperties False by the registry default, so the new tool is closed); R19-4 vidbee pack (2026-11-12): 744 -> 745 — media_chat added (additionalProperties False by the registry default, so the new tool is closed); R19-5 invoify pack (2026-11-12): 745 -> 746 — invoice added (additionalProperties False by the registry default, so the new tool is closed); R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing
        names = {d["name"] for d in decls}
        self.assertIn("technical_analysis", names)
        props = next(d for d in decls if d["name"] == "technical_analysis")
        self.assertEqual(len(props["parameters"]["properties"]), 9)
        self.assertEqual(props["parameters"]["required"], ["action"])

    def test_audit_totals(self):
        text = (TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        for needle in ('"total": 758', '"read": 383',  # INTENTIONAL — R18-8 ta pack (2026-11-12): audit needles 740/358/292 -> 741/359/293; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing
                       '"effectful": 375', '"confirm_gated": 166',  # INTENTIONAL — R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374/164 -> 375/165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling
                       '"already_verified_in_handler": 309',  # INTENTIONAL — R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan
                       '"productivity": 146'):  # INTENTIONAL — R18-10 timetrace pack (2026-11-12): productivity 135 -> 136 (effectful 77 -> 78); R19-2 ortools pack (2026-11-12): productivity 136 -> 137 (effectful 78 -> 79); R19-3 aureuserp pack (2026-11-12): productivity 137 -> 138 (effectful 79 -> 80); R19-5 invoify pack (2026-11-12): productivity 138 -> 139 (effectful 80 -> 81); R19-6 tolgee pack (2026-11-12): productivity 139 -> 140 (effectful 81 -> 82); R19-7 spoolman pack (2026-11-12): productivity 140 -> 141 (effectful 82 -> 83); R19-8 djangocrm pack (2026-11-12): productivity 141 -> 142 (effectful 83 -> 84); R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 -> 145 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 -> 146 — one new tool run_plan
            self.assertIn(needle, text, needle)
        totals = json.loads(re.search(
            r"<!-- AUDIT-TOTALS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(totals["total"], 758)  # INTENTIONAL — R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertEqual(totals["read"], 383)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 382 -> 383 — one new read tool get_morning_briefing
        self.assertEqual(totals["effectful"], 375)  # INTENTIONAL — R18-10 timetrace pack (2026-11-12): 359 -> 360; R19-1 videolingo pack (2026-11-12): 360 -> 361; R19-2 ortools pack (2026-11-12): 361 -> 362; R19-3 aureuserp pack (2026-11-12): 362 -> 363; R19-4 vidbee pack (2026-11-12): 363 -> 364; R19-5 invoify pack (2026-11-12): 364 -> 365; R19-6 tolgee pack (2026-11-12): 365 -> 366; R19-7 spoolman pack (2026-11-12): 366 -> 367; R19-8 djangocrm pack (2026-11-12): 367 -> 368; R19-10 vroom pack (2026-11-12): 368 -> 369; GAPS-7 connectors pack (2026-11-13): 369 -> 370; SOCIAL Phase 1 (2026-11-13): 370 -> 371; GAPS-8 scheduled-agents pack (2026-11-13): 371 -> 372; Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374 -> 375 — one new tool run_plan
        self.assertEqual(totals["confirm_gated"], 166)  # INTENTIONAL — R19-1 videolingo pack (2026-11-12): 158 -> 159; R19-4 vidbee pack (2026-11-12): 159 -> 160 — media_chat is confirm-gated for ask/summarize; R19-5 invoify pack (2026-11-12): 160 -> 161 — invoice is confirm-gated for send; R19-6 tolgee pack (2026-11-12): 161 -> 162 — translations is confirm-gated for translate; SOCIAL Phase 1 (2026-11-13): 162 -> 163 — social_send is confirm-gated; GAPS-8 scheduled-agents pack (2026-11-13): 163 -> 164 — scheduled_agents is confirm-gated; round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling
        self.assertEqual(totals["already_verified_in_handler"], 309)  # INTENTIONAL — R18-10 timetrace pack (2026-11-12): 293 -> 294; R19-1 videolingo pack (2026-11-12): 294 -> 295; R19-2 ortools pack (2026-11-12): 295 -> 296; R19-3 aureuserp pack (2026-11-12): 296 -> 297; R19-4 vidbee pack (2026-11-12): 297 -> 298; R19-5 invoify pack (2026-11-12): 298 -> 299; R19-6 tolgee pack (2026-11-12): 299 -> 300; R19-7 spoolman pack (2026-11-12): 300 -> 301; R19-8 djangocrm pack (2026-11-12): 301 -> 302; R19-10 vroom pack (2026-11-12): 302 -> 303; GAPS-7 connectors pack (2026-11-13): 303 -> 304; SOCIAL Phase 1 (2026-11-13): 304 -> 305; GAPS-8 scheduled-agents pack (2026-11-13): 305 -> 306; Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan
        counts = json.loads(re.search(
            r"<!-- AUDIT-COUNTS: (\{.*?\}) -->", text).group(1))
        self.assertEqual(counts["productivity"], 146)  # INTENTIONAL — R18-10 timetrace pack (2026-11-12): productivity 135 -> 136 (effectful 77 -> 78); R19-2 ortools pack (2026-11-12): productivity 136 -> 137 (effectful 78 -> 79); R19-3 aureuserp pack (2026-11-12): productivity 137 -> 138 (effectful 79 -> 80); R19-5 invoify pack (2026-11-12): productivity 138 -> 139 (effectful 80 -> 81); R19-6 tolgee pack (2026-11-12): productivity 139 -> 140 (effectful 81 -> 82); R19-7 spoolman pack (2026-11-12): productivity 140 -> 141 (effectful 82 -> 83); R19-8 djangocrm pack (2026-11-12): productivity 141 -> 142 (effectful 83 -> 84); R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 -> 145 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 -> 146 — one new tool run_plan
        row = next(line for line in text.splitlines()
                   if line.startswith("| productivity |"))
        self.assertIn("| 146 | 88 |", row)  # INTENTIONAL — R18-10 timetrace pack (2026-11-12): productivity 135 -> 136 (effectful 77 -> 78); R19-2 ortools pack (2026-11-12): productivity 136 -> 137 (effectful 78 -> 79); R19-3 aureuserp pack (2026-11-12): productivity 137 -> 138 (effectful 79 -> 80); R19-5 invoify pack (2026-11-12): productivity 138 -> 139 (effectful 80 -> 81); R19-6 tolgee pack (2026-11-12): productivity 139 -> 140 (effectful 81 -> 82); R19-7 spoolman pack (2026-11-12): productivity 140 -> 141 (effectful 82 -> 83); R19-8 djangocrm pack (2026-11-12): productivity 141 -> 142 (effectful 83 -> 84); R19-10 vroom pack (2026-11-12): productivity 142 -> 143 (effectful 84 -> 85); GAPS-8 scheduled-agents pack (2026-11-13): productivity 143 -> 144 (effectful 85 -> 86); field-check fix #32 (2026-11-18): 144 | 86 -> 145 | 87 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 145 | 87 -> 146 | 88 — one new tool run_plan


class TestIndicators(TaTestCase):
    def test_indicators_list(self):
        out = ta.technical_analysis(action="indicators")
        self.assertIn("25 indicators", out)
        self.assertEqual(len(ta.INDICATORS), 25)
        groups = {}
        for name, (group, _d, _f, _p) in ta.INDICATORS.items():
            groups.setdefault(group, []).append(name)
        self.assertEqual(len(groups["Volume"]), 5)
        self.assertEqual(len(groups["Volatility"]), 4)
        self.assertEqual(len(groups["Trend"]), 8)
        self.assertEqual(len(groups["Momentum"]), 6)
        self.assertEqual(len(groups["Others"]), 2)

    def test_known_values_on_rising_series(self):
        closes = [100.0 + i for i in range(40)]
        self.assertEqual(ta._sma(closes, 20)[-1], 129.5)
        self.assertEqual(ta._rsi(closes, 14)[-1], 100.0)
        upper, mid, lower = ta._bollinger(closes, 20, 2.0)
        self.assertEqual(mid[-1], 129.5)
        self.assertAlmostEqual(upper[-1], 141.33, places=2)
        self.assertAlmostEqual(lower[-1], 117.67, places=2)
        self.assertAlmostEqual(ta._ema(closes, 20)[-1], 129.5, places=2)
        line, sig, hist = ta._macd(closes)
        self.assertAlmostEqual(line[-1], 7.0, places=2)
        self.assertGreater(line[-1], sig[-1])

    def test_flat_series_edge_cases(self):
        flat = [10.0] * 30
        upper, mid, lower = ta._bollinger(flat, 20, 2.0)
        self.assertEqual((upper[-1], mid[-1], lower[-1]),
                         (10.0, 10.0, 10.0))
        self.assertEqual(ta._rsi(flat, 14)[-1], 50.0)
        self.assertEqual(ta._williams_r(flat, flat, flat, 14)[-1], -50.0)

    def test_warm_up_is_honest(self):
        out = ta.technical_analysis(action="compute", indicator="rsi",
                                    candles=json.dumps(_rising(5)))
        self.assertIn("needs more candles", out)

    def test_unknown_indicator_and_bad_params_refused(self):
        out = ta.technical_analysis(action="compute", indicator="bogus",
                                    candles=self.candles_json)
        self.assertIn("needs a known 'indicator'", out)
        out = ta.technical_analysis(action="compute", indicator="rsi",
                                    candles=self.candles_json,
                                    params='{"window": 1}')
        self.assertIn("must be between 2 and 500", out)
        out = ta.technical_analysis(action="compute", indicator="rsi",
                                    candles=self.candles_json,
                                    params='{"bogus": 3}')
        self.assertIn("unknown param", out)
        out = ta.technical_analysis(action="compute", indicator="macd",
                                    candles=self.candles_json,
                                    params='{"fast": 26, "slow": 12}')
        self.assertIn("'fast' must be smaller than 'slow'", out)


class TestCompute(TaTestCase):
    def test_compute_inline_and_dataset(self):
        out = ta.technical_analysis(action="compute", indicator="rsi",
                                    candles=self.candles_json)
        self.assertIn("rsi (40 candles", out)
        self.assertIn("latest 100.0000", out)
        self._add_dataset()
        out = ta.technical_analysis(action="compute", indicator="sma",
                                    dataset_id=1, limit=3)
        self.assertIn("sma (40 candles", out)
        self.assertIn("latest 129.5000", out)
        self.assertIn("Source: dataset 1 (Rising)", out)

    def test_compute_multi_series(self):
        out = ta.technical_analysis(action="compute", indicator="macd",
                                    candles=self.candles_json)
        self.assertIn("macd: latest", out)
        self.assertIn("signal: latest", out)
        self.assertIn("histogram: latest", out)
        out = ta.technical_analysis(action="compute",
                                    indicator="bollinger",
                                    candles=self.candles_json)
        self.assertIn("upper: latest", out)
        self.assertIn("middle: latest", out)
        self.assertIn("lower: latest", out)

    def test_compute_limit_and_unknown_dataset(self):
        out = ta.technical_analysis(action="compute", indicator="sma",
                                    candles=self.candles_json, limit=2)
        self.assertIn("last 2:", out)
        out = ta.technical_analysis(action="compute", indicator="sma",
                                    dataset_id=99)
        self.assertIn("no dataset 99", out)

    def test_signals_are_readings_not_advice(self):
        out = ta.technical_analysis(action="signals",
                                    candles=json.dumps(_mixed()))
        self.assertIn("NOT advice", out)
        self.assertIn("NOT a recommendation", out)
        self.assertIn("RSI(14)", out)
        self.assertIn("MACD", out)
        out = ta.technical_analysis(action="signals",
                                    candles=self.candles_json)
        self.assertIn("above the usual 70", out)
        out = ta.technical_analysis(action="signals",
                                    candles=json.dumps(_rising(10)))
        self.assertIn("needs at least 30 candles", out)


class TestDatasets(TaTestCase):
    def test_add_list_get_remove(self):
        self._add_dataset()
        out = ta.technical_analysis(action="dataset", mode="list")
        self.assertIn("#1 Rising (40 candles)", out)
        out = ta.technical_analysis(action="dataset", mode="get",
                                    dataset_id=1)
        self.assertIn("dataset #1 'Rising': 40 candles", out)
        self.assertIn("last close 139.0000", out)
        out = ta.technical_analysis(action="dataset", mode="remove",
                                    dataset_id=1)
        self.assertIn("removed", out)
        out = ta.technical_analysis(action="dataset", mode="list")
        self.assertIn("no datasets stored yet", out)

    def test_duplicate_name_refused(self):
        self._add_dataset()
        out = ta.technical_analysis(action="dataset", mode="add",
                                    name="Rising",
                                    candles=self.candles_json)
        self.assertIn("already exist", out)

    def test_bad_candles_refused(self):
        out = ta.technical_analysis(action="dataset", mode="add",
                                    name="Bad", candles="not json")
        self.assertIn("not valid JSON", out)
        bad = json.dumps([{"open": 1, "high": 0.5, "low": 2,
                           "close": 1}])
        out = ta.technical_analysis(action="dataset", mode="add",
                                    name="Bad", candles=bad)
        self.assertIn("do not bracket", out)
        bad = json.dumps([{"open": 1, "high": 2, "low": 0.5,
                           "close": 1, "volume": -5}])
        out = ta.technical_analysis(action="dataset", mode="add",
                                    name="Bad", candles=bad)
        self.assertIn("volume is negative", out)
        bad = json.dumps([{"open": 1, "high": 2, "low": 0.5}])
        out = ta.technical_analysis(action="dataset", mode="add",
                                    name="Bad", candles=bad)
        self.assertIn("missing open/high/low/close", out)

    def test_reads_never_create_the_store_file(self):
        self.assertFalse(Path(self.db).exists())
        ta.technical_analysis(action="status")
        ta.technical_analysis(action="dataset", mode="list")
        ta.technical_analysis(action="events")
        self.assertFalse(Path(self.db).exists(),
                         "a read created the store file")

    def test_write_creates_and_verifies(self):
        self._add_dataset()
        self.assertTrue(Path(self.db).exists())
        store = ta._TaStore(self.db)
        self.assertTrue(store.verify_chain())


class TestLedger(TaTestCase):
    def test_events_verify_and_record_everything(self):
        out = ta.technical_analysis(action="events")
        self.assertIn("ledger is empty", out)
        self._add_dataset()
        out = ta.technical_analysis(action="events")
        self.assertIn("dataset_add", out)
        self.assertIn("chain OK", out)

    def test_ledger_tamper_is_caught(self):
        self._add_dataset()
        store = ta._TaStore(self.db)
        self.assertTrue(store.verify_chain())
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE events SET payload = ? WHERE id = 1",
                     ('{"id": 1, "name": "Forged", "rows": 1}',))
        conn.commit()
        conn.close()
        self.assertFalse(ta._TaStore(self.db).verify_chain())
        out = ta.technical_analysis(action="events")
        self.assertIn("chain BROKEN", out)


class TestGates(TaTestCase):
    def test_free_by_design_no_confirmation(self):
        self.assertIn("technical_analysis", _FREE_BY_DESIGN)
        self.assertEqual(_FREE_BY_DESIGN["technical_analysis"],
                         "app-state")
        self.assertNotIn("technical_analysis", _CONFIRM_GATE_TOOLS)
        self.assertNotIn("technical_analysis", _READ_ONLY_TOOLS)
        self.assertFalse(_needs_confirmation(
            "technical_analysis",
            {"action": "dataset", "mode": "add"}))

    def test_classification_snapshot(self):
        snap = json.loads((TREE / "tests" / "_classification_snapshot.json"
                           ).read_text(encoding="utf-8"))
        self.assertEqual(snap["technical_analysis"],
                         {"confirm": False, "free": "app-state",
                          "read": False})

    def test_catalogue_has_technical_analysis_in_your_world(self):
        # With the rest of the finance family (billing, wardrobe),
        # not in "Everyday skills".
        self.assertIn("technical_analysis",
                      GROUP_MEMBERS["Your world"])

    def test_licence_marker_is_proposed_or_parent_ruled(self):
        # Hardened (the R18-1/R18-5 trap): the parent's ruling edit
        # flips the marker after this run, so either state passes.
        text = (TREE / "core" / "technical_analysis.py").read_text(
            encoding="utf-8")
        self.assertTrue("PROPOSED — parent rules" in text
                        or "Parent-ruled" in text)

    def test_no_network_or_feed_in_module(self):
        # Import/call sites only — the docstring names yfinance as
        # a dropped source, so a whole-file word grep would false-
        # positive on the prose.
        text = (TREE / "core" / "technical_analysis.py").read_text(
            encoding="utf-8")
        for needle in ("import urllib", "from urllib", "import requests",
                       "import yfinance", "import socket",
                       "http.client", "urlopen"):
            self.assertNotIn(needle, text, needle)


if __name__ == "__main__":
    unittest.main()
