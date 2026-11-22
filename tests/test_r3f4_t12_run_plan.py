"""Round-3 fix #4 (T12): run_plan is a real voice tool.

Section-A finding A-RU1 (BLOCKING): `run_plan` was not a
registered tool and not offered in the desktop profile — a
live "run my morning plan" produced zero tool calls.

This suite pins the ruling (parent, 2026-11-17): `run_plan`
becomes a real voice tool, confirm-gated, owner-created plans
only; `run_goal` stays unreachable.

The store is a temp file per test (core/brain/planner_plans
_PLANS_PATH patched), so no plan ever touches data/plans.json;
setUpModule/tearDownModule also back up and restore the whole
data/ directory, the test_anticipation_executor pattern.
"""

import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from core import confirm_gate as cg
from core.brain import planner
from core.brain import planner_plans
from core.confirm_gate_allowlist import kind_args_check, passes_safe_shape
from core.funnel import tool_classification as tc
from core.funnel.tool_profiles import DESKTOP_TOOL_PRIORITY
from core.funnel.toolbox_tokens import CORE_TOOL_PRIORITY
from core.funnel.tool_profiles import desktop_declarations
from core.funnel.tools_registry import tools as TOOLS

ROOT = pathlib.Path(__file__).resolve().parents[1]

_DATA_BACKUP = None


def setUpModule():
    global _DATA_BACKUP
    data_dir = ROOT / "data"
    _DATA_BACKUP = tempfile.mkdtemp(prefix="r3f4_data_")
    if data_dir.is_dir():
        shutil.copytree(data_dir, _DATA_BACKUP, dirs_exist_ok=True)


def tearDownModule():
    global _DATA_BACKUP
    if _DATA_BACKUP is not None:
        data_dir = ROOT / "data"
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.copytree(_DATA_BACKUP, data_dir)
        shutil.rmtree(_DATA_BACKUP, ignore_errors=True)
    _DATA_BACKUP = None
    cg.register_handler(None)


def _decls():
    return {d["name"]: d for d in TOOLS.get_declarations()}


def _approve_all():
    cg.register_handler(lambda *a, **k: True)


def _no_handler():
    cg.register_handler(None)


class RunPlanToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._plans_path = (pathlib.Path(self._tmp.name)
                            / "plans.json")
        # The store path is read through FOUR module attributes:
        # loads resolve it through the facade (planner._PLANS_PATH,
        # the B23 live-read pattern in planner_storage._load_plans)
        # while saves use each family's own imported copy
        # (planner_plans, planner_checkpoint). All four must
        # point at the same temp file or create and run see
        # different stores.
        from core.brain import planner_checkpoint, planner_storage
        self._patchers = []
        for module in (planner, planner_plans, planner_checkpoint,
                       planner_storage):
            patcher = mock.patch.object(
                module, "_PLANS_PATH", self._plans_path)
            patcher.start()
            self._patchers.append(patcher)
        for patcher in self._patchers:
            self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        _approve_all()
        self.addCleanup(_no_handler)

    def _make_plan(self, goal="fieldcheck morning", steps=None):
        if steps is None:
            steps = [
                {"text": "Check the time",
                 "tool": "get_current_time_date", "args": {}},
                {"text": "Do a sum",
                 "tool": "quick_math", "args": {"expression": "2+2"}},
            ]
        result = planner.create_plan(goal, steps)
        self.assertIn("Plan plan-1 created", result)
        return "plan-1"

    # -- registration surfaces -------------------------------------

    def test_run_plan_registered_with_plan_id_only(self):
        decls = _decls()
        self.assertIn("run_plan", decls)
        decl = decls["run_plan"]
        self.assertEqual(decl["parameters"]["required"], ["plan_id"])
        # Owner-created ONLY, by shape: the tool takes exactly
        # one argument, the plan id. No goal, no steps — a
        # model-invented plan cannot be handed to it.
        self.assertEqual(
            set(decl["parameters"]["properties"]), {"plan_id"})
        self.assertIs(
            decl["parameters"].get("additionalProperties"), False)

    def test_census_moved_to_769_tools(self):  #Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertEqual(len(TOOLS.get_declarations()), 769)  # INTENTIONAL; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_desktop_profile_offers_run_plan(self):
        decls = desktop_declarations(TOOLS)
        names = [d["name"] for d in decls]
        self.assertIn("run_plan", names)
        self.assertEqual(len(names), 76)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 59 -> 60 — one new tool get_morning_briefing joins the desktop profile; round-3 fix Q5+Q7 (2026-11-19): 60 -> 65 — remember_user_fact, recall_memories, quick_math, run_routine, save_routine join the desktop profile; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        self.assertIn("run_plan", DESKTOP_TOOL_PRIORITY)
        self.assertIn("run_plan", CORE_TOOL_PRIORITY)

    # -- confirm gate ----------------------------------------------

    def test_run_plan_is_confirm_gated(self):
        self.assertEqual(tc.classify("run_plan"), "confirm")
        call = tc.classify_call("run_plan", {"plan_id": "plan-1"})
        self.assertTrue(call.needs_confirmation)
        self.assertFalse(call.is_read)

    def test_run_plan_is_never_always_allowable(self):
        # Membership in _CONFIRM_GATE_TOOLS bars the always-allow
        # shortcut structurally (confirm_gate_allowlist returns
        # "" for every confirm-gated tool).
        self.assertEqual(kind_args_check("run_plan"), "")
        self.assertFalse(passes_safe_shape("run_plan"))

    def test_run_plan_refuses_without_a_confirm_handler(self):
        self._make_plan()
        _no_handler()
        result = TOOLS.execute("run_plan", {"plan_id": "plan-1"})
        self.assertEqual(result.get("kind"), "not_confirmed")
        self.assertEqual(result.get("status"), "refused")

    # -- owner-created only ----------------------------------------

    def test_unknown_plan_id_is_refused_and_nothing_runs(self):
        result = TOOLS.execute("run_plan", {"plan_id": "plan-999"})
        text = result.get("result") or result.get("message") or ""
        self.assertIn("No plan found", text)
        self.assertIn("plan-999", text)
        self.assertEqual(result.get("status"), "success")

    def test_handler_refuses_unknown_plan_directly(self):
        text = planner.run_plan_tool("plan-999")
        self.assertIn("No plan found", text)
        self.assertIn("plan-999", text)

    # -- the run itself --------------------------------------------

    def test_handler_runs_a_saved_plan_to_completion(self):
        plan_id = self._make_plan()
        result = TOOLS.execute("run_plan", {"plan_id": plan_id})
        text = result.get("result") or result.get("message") or ""
        self.assertEqual(result.get("status"), "success")
        self.assertIn("Plan plan-1 finished: 2/2 step(s) done.", text)
        progress = planner.plan_progress(plan_id)
        self.assertEqual(progress["done"], 2)
        self.assertTrue(progress["complete"])

    def test_undeclared_step_is_resolved_at_run_time(self):
        # A plain-language step (no tool named) is resolved by
        # the fix #32 production executor at RUN time — the same
        # resolver as run_anticipation_routine — never guessed.
        planner.create_plan("plain morning", [
            {"text": "Tell me the time"},
        ])
        result = TOOLS.execute("run_plan", {"plan_id": "plan-1"})
        text = result.get("result") or result.get("message") or ""
        self.assertEqual(result.get("status"), "success")
        self.assertIn("Plan plan-1 finished: 1/1 step(s) done.", text)
        progress = planner.plan_progress("plan-1")
        self.assertEqual(progress["done"], 1)

    def test_unresolvable_step_halts_naming_the_step(self):
        planner.create_plan("wobble", [
            {"text": "Do the thing with the wobble"},
        ])
        result = TOOLS.execute("run_plan", {"plan_id": "plan-1"})
        text = result.get("result") or result.get("message") or ""
        self.assertIn("halted", text)
        self.assertIn("wobble", text)
        progress = planner.plan_progress("plan-1")
        self.assertEqual(progress["done"], 0)

    def test_confirm_gated_step_pauses_the_plan(self):
        # The run approves STARTING the plan, never its steps:
        # the handler approves run_plan itself but denies the
        # confirm-gated step inside it, so the plan starts, the
        # first step runs, and the second step pauses the plan
        # there — never auto-confirmed.
        planner.create_plan("gated morning", [
            {"text": "Check the time",
             "tool": "get_current_time_date", "args": {}},
            {"text": "Run a shell command",
             "tool": "run_command", "args": {"command": "echo hi"}},
        ])
        cg.register_handler(lambda name, *a, **k: name == "run_plan")
        result = TOOLS.execute("run_plan", {"plan_id": "plan-1"})
        text = result.get("result") or result.get("message") or ""
        self.assertIn("halted", text)
        self.assertIn("Plan stopped at step 2 of 2", text)
        progress = planner.plan_progress("plan-1")
        self.assertEqual(progress["done"], 1)
        self.assertFalse(progress["complete"])

    def test_nested_routine_step_is_refused_by_the_walk(self):
        planner.create_plan("nested", [
            {"text": "Run the morning routine",
             "tool": "run_routine", "args": {"name": "morning"}},
        ])
        result = TOOLS.execute("run_plan", {"plan_id": "plan-1"})
        text = result.get("result") or result.get("message") or ""
        self.assertIn("cannot nest", text)
        progress = planner.plan_progress("plan-1")
        self.assertEqual(progress["done"], 0)

    # -- run_goal stays unreachable --------------------------------

    def test_run_goal_stays_unreachable(self):
        decls = _decls()
        self.assertNotIn("run_goal", decls)
        names = [d["name"] for d in desktop_declarations(TOOLS)]
        self.assertNotIn("run_goal", names)
        self.assertNotIn("run_goal", DESKTOP_TOOL_PRIORITY)
        self.assertNotIn("run_goal", CORE_TOOL_PRIORITY)
        self.assertFalse(hasattr(planner, "run_goal_tool"))


if __name__ == "__main__":
    unittest.main()
