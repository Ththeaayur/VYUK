"""Round-3 fix Q5+Q7 (2026-11-19, parent ruling: merge — add the
memory tools and the routine pair to DESKTOP_TOOL_PRIORITY).

Section B, parent-verified: Q5 — the memory tools are absent from
the desktop profile (remember_user_fact, recall_memories, quick_math
not in the resident set); Q7 — planner routines are unreachable by
natural voice (run_routine not resident; the model defaults to
run_anticipation_routine, the wrong tool for a named routine). The
desktop profile is the default Live profile, so a tool absent from
it can only be reached via use_toolbox, which the model does not
route a by-name demand through (field-check fix #10, V2 — the
get_current_time_date precedent, same shape as run_plan in fix #4
and run_anticipation_routine in fix #32).

This suite pins the fix, so it can never regress silently:

- The five tools are resident in the desktop profile (48 -> 53);
  the desktop offered set is 71 (59 resident + 11 routers +
  use_toolbox). The four already-core tools keep their core home
  (61, +find_free_slots); save_routine's home is desktop-only — its Mind
  siblings split the same way (run/list in core, save/delete in
  neither), so the assistant offered set moves 61 -> 62. No confirm
  gate is added or removed: save_routine stays gated, run_routine
  stays ungated.
- The lite router sends the spoken asks to the right tool:
  "remember that ..." -> remember_user_fact, "what do you
  remember about X" -> recall_memories, "what's 2+2" /
  "calculate 12 * 3" -> quick_math, "run my morning routine"
  -> run_routine (never run_anticipation_routine). A bare "run
  my routine" (no name) and "save this routine" (no name, no
  actions) stay unrouted — the router never invents arguments;
  save_routine's routing proof is the toolbox ranking, where it
  already ranks first.
- The toolbox ranking surfaces the right tool first for the
  named asks (run/save/recall/calculate).
- The tools work when called: quick_math evaluates; a
  remember/recall round-trip through the funnel finds the fact;
  a save/run round-trip executes the saved step.

Stdlib unittest only. The round-trips run against temp stores
(a temp MemoryStore for the memory pair; a temp routines.json
for the routine pair, patched into both the save path and the
facade load path). The funnel executes write receipts/records
as every funnel-driving suite does; the leg harness restores
data/ afterwards.
"""

import json
import pathlib
import tempfile
import unittest
from unittest import mock

import core.brain.planner as _planner_facade  # noqa: E402
import core.brain.planner_routines as _routines  # noqa: E402
import core.funnel.tools_handlers_command_url as _cmdurl  # noqa: E402
from core.funnel.tool_classification import (  # noqa: E402
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
)
from core.funnel.tool_profiles import (  # noqa: E402
    DESKTOP_TOOL_PRIORITY,
    profile_declarations,
)
from core.funnel.toolbox_tokens import (  # noqa: E402
    CORE_TOOL_PRIORITY,
    rank_tools_keyword,
)
from core.funnel import tools_gate as _tools_gate  # noqa: E402
from core.funnel.tools_registry import tools  # noqa: E402
from core.memory.memory import MemoryStore  # noqa: E402
from core.skills_smart import quick_math  # noqa: E402
from core.voice.lite_client_helpers import _route_intent  # noqa: E402

FIVE = ("remember_user_fact", "recall_memories", "quick_math",
        "run_routine", "save_routine")


class TestDesktopResidency(unittest.TestCase):
    def test_five_tools_resident_in_desktop(self):
        for tool in FIVE:
            self.assertIn(tool, DESKTOP_TOOL_PRIORITY, tool)

    def test_desktop_resident_count_is_59(self):  #Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(
            len(DESKTOP_TOOL_PRIORITY), 59,  #Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
            "round-3 fix Q5+Q7 (2026-11-19): 48 -> 53 — the five "
            "memory/routine tools join the desktop profile")

    def test_four_keep_their_core_home(self):
        for tool in ("remember_user_fact", "recall_memories",
                     "quick_math", "run_routine"):
            self.assertIn(tool, CORE_TOOL_PRIORITY, tool)

    def test_save_routine_home_is_desktop_only(self):
        # Its Mind siblings split the same way (run/list in
        # core, save/delete in neither); no new rule invented.
        self.assertNotIn("save_routine", CORE_TOOL_PRIORITY)

    def test_core_resident_count_is_61(self):  #Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169
        self.assertEqual(len(CORE_TOOL_PRIORITY), 61)  #Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169

    def test_every_desktop_resident_is_registered(self):
        names = {d["name"] for d in tools.get_declarations()}
        for tool in DESKTOP_TOOL_PRIORITY:
            self.assertIn(tool, names, tool)

    def test_desktop_offered_is_71(self):  #Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        decls = profile_declarations(tools, "desktop")
        names = [d["name"] for d in decls]
        self.assertEqual(len(names), 71)  # 59 resident + 11 routers; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        # + use_toolbox; round-3 fix Q5+Q7 (2026-11-19): 60 -> 65
        self.assertEqual(names[-1], "use_toolbox")
        for tool in FIVE:
            self.assertIn(tool, names, tool)

    def test_assistant_offered_is_73(self):  #Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169
        decls = profile_declarations(tools, "assistant")
        names = [d["name"] for d in decls]
        # 61 resident + 11 routers + use_toolbox (the "61 offered; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169
        # declarations" pin elsewhere counts router_declarations:
        # 61 resident + use_toolbox, no routers — see below).; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169
        self.assertEqual(len(names), 73)  #Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169
        self.assertEqual(names[-1], "use_toolbox")

    def test_router_declarations_is_62(self):  #Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169
        from core.funnel.toolbox import router_declarations
        decls = router_declarations(tools)
        names = [d["name"] for d in decls]
        self.assertEqual(len(names), 62)  # 61 resident + use_toolbox; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169


class TestClassificationUnmoved(unittest.TestCase):
    def test_save_routine_still_confirm_gated(self):
        self.assertIn("save_routine", _CONFIRM_GATE_TOOLS)
        self.assertNotIn("save_routine", _READ_ONLY_TOOLS)

    def test_run_routine_still_not_confirm_gated(self):
        # This fix adds NO gate: run_routine keeps the gate it
        # has today (none — it dispatches steps that each keep
        # their own gate).
        self.assertNotIn("run_routine", _CONFIRM_GATE_TOOLS)
        self.assertNotIn("run_routine", _READ_ONLY_TOOLS)
        self.assertIn("run_routine", _FREE_BY_DESIGN)


class TestLiteRouting(unittest.TestCase):
    def test_remember(self):
        self.assertEqual(
            _route_intent("remember that I like strong coffee"),
            ("remember_user_fact",
             {"key": "note", "value": "i like strong coffee"}))

    def test_recall_about(self):
        self.assertEqual(
            _route_intent("what do you remember about my coffee"),
            ("recall_memories", {"query": "my coffee"}))

    def test_recall_name(self):
        self.assertEqual(
            _route_intent("what is my name"),
            ("recall_memories", {"query": "name"}))

    def test_maths_whats(self):
        self.assertEqual(_route_intent("what's 2+2"),
                         ("quick_math", {"expression": "2+2"}))

    def test_maths_calculate(self):
        self.assertEqual(_route_intent("calculate 12 * 3"),
                         ("quick_math", {"expression": "12 * 3"}))

    def test_maths_divide(self):
        self.assertEqual(_route_intent("what is 12 / 4"),
                         ("quick_math", {"expression": "12 / 4"}))

    def test_maths_how_much(self):
        self.assertEqual(_route_intent("how much is 3 * 7"),
                         ("quick_math", {"expression": "3 * 7"}))

    def test_maths_does_not_steal_time(self):
        self.assertEqual(_route_intent("what's the time"),
                         ("get_current_time_date", {}))

    def test_maths_does_not_steal_briefing(self):
        self.assertEqual(_route_intent("what's on today"),
                         ("get_morning_briefing", {}))

    def test_maths_does_not_steal_name(self):
        self.assertEqual(_route_intent("what's my name"),
                         ("recall_memories", {"query": "name"}))

    def test_run_named_routine(self):
        self.assertEqual(_route_intent("run my morning routine"),
                         ("run_routine", {"name": "morning"}))

    def test_run_routine_without_possessive(self):
        self.assertEqual(_route_intent("run evening routine"),
                         ("run_routine", {"name": "evening"}))

    def test_run_routine_never_steals_anticipation(self):
        got = _route_intent("run my anticipation routine")
        self.assertTrue(got is None or got[0] != "run_routine", got)

    def test_run_bare_routine_unrouted(self):
        # No name to run — the router never invents one.
        self.assertIsNone(_route_intent("run my routine"))
        self.assertIsNone(_route_intent("run the routine"))

    def test_save_unrouted_in_lite_router(self):
        # No name and no actions to save — the router never
        # invents either; save_routine's routing proof is the
        # toolbox ranking (test below), where it ranks first.
        self.assertIsNone(_route_intent("save this routine"))

    def test_recall_bare_unrouted(self):
        # No topic to search for.
        self.assertIsNone(_route_intent("what do you remember"))

    def test_bare_digits_unrouted(self):
        # A year or a phone number is not a maths ask.
        self.assertIsNone(_route_intent("2026"))
        self.assertIsNone(_route_intent("1-800-555-1234"))


class TestToolboxRanking(unittest.TestCase):
    def test_run_named_routine_first(self):
        self.assertEqual(
            rank_tools_keyword(tools, "run my morning routine",
                               limit=1), ["run_routine"])

    def test_save_first(self):
        self.assertEqual(
            rank_tools_keyword(tools, "save this routine",
                               limit=1), ["save_routine"])

    def test_recall_about_first(self):
        self.assertEqual(
            rank_tools_keyword(
                tools, "what do you remember about my coffee",
                limit=1), ["recall_memories"])

    def test_maths_calculate_first(self):
        self.assertEqual(
            rank_tools_keyword(tools, "calculate 2+2",
                               limit=1), ["quick_math"])


class TestHandlerSmoke(unittest.TestCase):
    def setUp(self):
        # Loophole batch 4: the speaker lock defaults ON, so a
        # session with no provenance ("unknown") now gates every
        # state-changing tool — remember_user_fact would be
        # refused before the handler ran. These legs measure the
        # handler round-trips, so they run in a user-started
        # session.
        super().setUp()
        _tools_gate.set_session_provenance("user")

    def tearDown(self):
        _tools_gate.set_session_provenance("unknown")
        super().tearDown()

    def test_quick_math_evaluates(self):
        self.assertEqual(quick_math("2+2"), "2+2 = 4")
        self.assertEqual(quick_math("12 * 3"), "12 * 3 = 36")

    def test_quick_math_refusal_is_a_failure_dict(self):
        out = quick_math("2+")
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("status"), "failure")
        self.assertEqual(out.get("kind"), "invalid_expression")

    def test_remember_recall_round_trip(self):
        tmp = tempfile.mkdtemp(prefix="r3q57_memory_")
        store = MemoryStore(str(pathlib.Path(tmp) / "assistant.db"))
        with mock.patch.object(_cmdurl, "memory_store", store):
            saved = tools.execute(
                "remember_user_fact",
                {"key": "note", "value": "i like strong coffee"})
            self.assertEqual(saved.get("status"), "success", saved)
            self.assertIn("Saved to memory", saved.get("result", ""))
            found = tools.execute("recall_memories",
                                  {"query": "coffee"})
            self.assertEqual(found.get("status"), "success", found)
            self.assertIn("strong coffee", found.get("result", ""))

    def test_save_run_round_trip(self):
        tmp = tempfile.mkdtemp(prefix="r3q57_routine_")
        path = pathlib.Path(tmp) / "routines.json"
        # The save path reads planner_routines._ROUTINES_PATH;
        # the load path resolves through the planner facade.
        with (mock.patch.object(_routines, "_ROUTINES_PATH", path),
              mock.patch.object(_planner_facade, "_ROUTINES_PATH",
                                path)):
            saved = _routines.save_routine(
                "morning",
                actions=json.dumps(
                    [{"tool": "get_current_time_date", "args": {}}]))
            self.assertIn("saved with 1 action", saved, saved)
            ran = _routines.run_routine("morning")
            self.assertIn("all 1 action(s) completed", ran, ran)
            self.assertIn("get_current_time_date", ran, ran)


if __name__ == "__main__":
    unittest.main()
