"""Fix #32 follow-up (2026-11-19, parent ruling).

Pins the classification fix #32 left undone, so it can never
regress silently:

- run_anticipation_routine is CONFIRM-GATED, like run_plan.
  Fix #32 added the tool with a snapshot entry claiming
  "executor dispatch" but never put it in any gate set, so it
  ran ungated (freedom by omission) and six suites failed on
  the unclassified tool (gaps23, gaps29 x2, classification,
  regression_classification_snapshot, auditfix_batch2,
  teach_trigger). The parent ruling (2026-11-19): running a
  saved anticipation routine executes its steps, exactly like
  run_plan — same gate, same reason. This suite pins the
  membership, the snapshot value, and the confirm count (166).
- The teach ranking gap from the same batch: fix #32's new
  tool scored 24 for "teach me a routine" and pushed
  teach_list_sequences out of the top 8 (it scored only on
  its name, 12) — and lifting it alone would have pushed
  teach_replay_sequence out instead, so both teach tools got
  capability-phrase synonym rows (TOOL_SYNONYMS, the ROUTE-01
  precedent). This suite pins the surfacing and the two
  guard rails: "list my routines" still routes to
  list_routines first, "replay my routine" still routes to
  run_routine first.

Stdlib unittest only. No data/ writes (ranking + classification
reads only, plus the doctor label check, which is read-only).
"""

import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

from core.funnel.tool_classification import (  # noqa: E402
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
)
from core.funnel.tools_gate import _needs_confirmation  # noqa: E402
from core.funnel.tools_registry import tools  # noqa: E402
from core.funnel import toolbox as _toolbox  # noqa: E402

_SNAPSHOT = ROOT / "tests" / "_classification_snapshot.json"

TEACH_TOOLS = ("teach_record_sequence", "teach_list_sequences",
               "teach_replay_sequence")


class TestAnticipationRoutineClassification(unittest.TestCase):
    def test_run_anticipation_routine_is_confirm_gated(self):
        self.assertIn("run_anticipation_routine", _CONFIRM_GATE_TOOLS)
        self.assertNotIn("run_anticipation_routine", _READ_ONLY_TOOLS)
        self.assertNotIn("run_anticipation_routine", _FREE_BY_DESIGN)
        self.assertTrue(_needs_confirmation("run_anticipation_routine", {}))

    def test_confirm_count_is_166(self):
        self.assertEqual(
            len(_CONFIRM_GATE_TOOLS), 166,
            "fix #32 follow-up (2026-11-19): 165 -> 166 — "
            "run_anticipation_routine classified confirm-gated "
            "per parent ruling")

    def test_snapshot_entry_is_confirm_gated(self):
        snap = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(
            snap["run_anticipation_routine"],
            {"confirm": True, "free": None, "read": False},
            "snapshot must agree with the code: the stale "
            "'executor dispatch' entry is what let the tool run "
            "ungated while the snapshot claimed it was classified")

    def test_run_plan_still_confirm_gated(self):
        # The ruling's precedent: the two routine runners share
        # the gate. If run_plan ever leaves it, this fails first.
        self.assertIn("run_plan", _CONFIRM_GATE_TOOLS)


class TestTeachRankingAfterAnticipationTool(unittest.TestCase):
    def test_teach_phrase_surfaces_all_teach_tools(self):
        ranked = _toolbox.rank_tools_keyword(
            tools, "teach me a routine", limit=8)
        for tool in TEACH_TOOLS:
            self.assertIn(tool, ranked, ranked)

    def test_list_my_routines_still_routes_to_list_routines(self):
        ranked = _toolbox.rank_tools_keyword(
            tools, "list my routines", limit=8)
        self.assertEqual(ranked[0], "list_routines", ranked)

    def test_replay_my_routine_still_routes_to_run_routine(self):
        ranked = _toolbox.rank_tools_keyword(
            tools, "replay my routine", limit=8)
        self.assertEqual(ranked[0], "run_routine", ranked)

    def test_show_taught_sequences_surfaces_list_tool(self):
        ranked = _toolbox.rank_tools_keyword(
            tools, "show my taught sequences", limit=8)
        self.assertEqual(ranked[0], "teach_list_sequences", ranked)

    def test_record_phrase_still_ranks_record_first(self):
        ranked = _toolbox.rank_tools_keyword(
            tools, "record a sequence", limit=8)
        self.assertEqual(ranked[0], "teach_record_sequence", ranked)


class TestDoctorLabelAfterFollowup(unittest.TestCase):
    def test_doctor_label_counts_sixty_offered(self):
        import doctor as doctor_mod
        seen = []
        saved = doctor_mod.report
        doctor_mod.report = lambda s, l, d="", f="": seen.append((s, l, d))
        try:
            doctor_mod.check_tool_router()
        finally:
            doctor_mod.report = saved
        router = [d for s, l, d in seen if l == "Tool router"]
        self.assertEqual(len(router), 1)
        detail = router[0]
        self.assertIn("61 offered declarations", detail)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 60 -> 61 — one new tool get_morning_briefing joins the routed core set
        self.assertIn("(60 resident tools + use_toolbox)", detail)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 59 -> 60 — one new tool get_morning_briefing joins the routed core set


if __name__ == "__main__":
    unittest.main()
