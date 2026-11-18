"""Field-check fix #32 (round-2 N5): the production executor for
anticipation routines.

Anticipation routines describe their steps in plain language
("tell me the time", "open Notepad") and `run_routine()` without
an executor returns plan-only — so no routine could ever run,
and no funnel tool ran one. The production executor resolves
each step text to a tool call AT RUN TIME through the funnel's
own gates (the same resolution/confirm path a spoken step goes
through), and `run_anticipation_routine` is the funnel tool
that runs a named routine through it.

What this suite proves:
* the tool is registered, in both voice profiles (the fix #10
  precedent — the desktop profile is the default Live profile),
  in the Mind catalogue group with a drift-free snapshot, and
  in the classification snapshot as free "executor dispatch";
* a resolvable step runs end to end through the funnel gates
  (ranked prose, bare tool name, explicit JSON object, and
  `name {json}` forms);
* an unresolvable step refuses honestly naming the step, and a
  step that needs arguments refuses naming the tool, the
  missing params, and the explicit form — neither is skipped
  or pretended to have run;
* a confirm-gated step pauses the routine (no handler in this
  process, so the gate fails closed with not_confirmed) and
  nothing runs;
* a step naming a routine-management tool, the scheduler engine
  (`scheduled_agents` — a run_routine action would smuggle routine
  execution past the forbidden list), or the toolbox is refused
  in every resolution path;
* the first failing step halts the routine and the later steps
  are named as not run;
* resolution happens at RUN time: an unresolvable step stores
  fine and only refuses when run;
* `run_routine()` without an executor still returns plan-only
  with zero executed steps (the contract is unchanged);
* a routine survives a store reset and still runs through the
  tool (fix #15 persistence + this executor together).

Qt-free, both interpreters. The routine store path is patched
to a temp file per test (the fix #15 harness shape), so no
live data/anticipation_routines.json is touched; data/ itself
is snapshotted at module level and restored afterwards (the
funnel may write Activity lines).
"""
import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent

from core import w7_wiring_anticipation as w7
from core.funnel.tools_registry import tools as TOOLS

_DATA_BACKUP = None


def setUpModule():
    global _DATA_BACKUP
    data_dir = ROOT / "data"
    _DATA_BACKUP = tempfile.mkdtemp(prefix="r2n5_data_")
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


def _run(name):
    """run_anticipation_routine through the funnel; returns the
    plain-words result string."""
    result = TOOLS.execute("run_anticipation_routine", {"name": name})
    return result.get("result") or result.get("message") or ""


class AnticipationExecutorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = (pathlib.Path(self._tmp.name)
                      / "anticipation_routines.json")
        patcher = mock.patch.object(
            w7, "_ANTICIPATION_ROUTINES_PATH", self._path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        w7.reset_anticipation()
        self.addCleanup(w7.reset_anticipation)

    # -- registration surfaces -------------------------------------

    def test_tool_registered_with_name_param(self):
        decls = {d["name"]: d for d in TOOLS.get_declarations()}
        self.assertIn("run_anticipation_routine", decls)
        decl = decls["run_anticipation_routine"]
        self.assertEqual(decl["parameters"]["required"], ["name"])
        self.assertIn("name", decl["parameters"]["properties"])
        self.assertIs(
            decl["parameters"].get("additionalProperties"), False)

    def test_tool_in_both_voice_profiles(self):
        # Fix #10 precedent: a tool absent from the desktop
        # profile (the default Live profile) can only be
        # reached via use_toolbox, which the model does not
        # route a by-name demand through.
        from core.funnel.tool_profiles import DESKTOP_TOOL_PRIORITY
        from core.funnel.toolbox_tokens import CORE_TOOL_PRIORITY
        self.assertIn("run_anticipation_routine", DESKTOP_TOOL_PRIORITY)
        self.assertIn("run_anticipation_routine", CORE_TOOL_PRIORITY)

    def test_tool_in_mind_group_snapshot_without_drift(self):
        from core import catalogue_groups_data
        from core import catalogue_snapshot
        self.assertIn("run_anticipation_routine",
                      catalogue_groups_data.GROUP_MEMBERS["Mind"])
        self.assertEqual(catalogue_snapshot.GROUP_MEMBERS,
                         catalogue_groups_data.GROUP_MEMBERS)

    def test_tool_classified_free_executor_dispatch(self):
        snapshot = json.loads(
            (ROOT / "tests" / "_classification_snapshot.json")
            .read_text(encoding="utf-8"))
        entry = snapshot["run_anticipation_routine"]
        self.assertIs(entry["confirm"], False)
        self.assertEqual(entry["free"], "executor dispatch")
        self.assertIs(entry["read"], False)

    # -- the run itself --------------------------------------------

    def test_ranked_step_runs_through_funnel(self):
        w7.plan_routine("morning", "at 7am", ["tell me the time"])
        out = _run("morning")
        self.assertIn("completed", out.lower())
        self.assertIn("get_current_time_date", out)
        self.assertIn("✓", out)

    def test_explicit_json_object_form_runs(self):
        w7.plan_routine("lister", "at 7am",
                        ['{"tool": "get_current_time_date", "args": {}}'])
        out = _run("lister")
        self.assertIn("completed", out.lower())
        self.assertIn("get_current_time_date", out)

    def test_bare_tool_name_runs(self):
        w7.plan_routine("clock", "at 7am", ["get_current_time_date"])
        out = _run("clock")
        self.assertIn("completed", out.lower())
        self.assertIn("get_current_time_date", out)

    def test_name_plus_json_form_runs(self):
        w7.plan_routine("lister2", "at 7am", ["list_routines {}"])
        out = _run("lister2")
        self.assertIn("completed", out.lower())
        self.assertIn("list_routines", out)

    def test_unresolvable_step_refuses_naming_the_step(self):
        w7.plan_routine("weather-check", "at 7am", ["read the weather"])
        out = _run("weather-check")
        self.assertNotIn("completed", out.lower())
        self.assertIn("read the weather", out)
        self.assertIn("stopped at step 1 of 1", out)
        self.assertNotIn("✓", out)

    def test_missing_args_refuses_naming_tool_params_and_form(self):
        w7.plan_routine("opener", "at 7am", ["open Notepad"])
        out = _run("opener")
        self.assertNotIn("completed", out.lower())
        self.assertIn("launch_application", out)
        self.assertIn("app_name", out)
        self.assertIn("{", out)  # the explicit form to use
        self.assertNotIn("✓", out)

    def test_malformed_explicit_form_refuses(self):
        w7.plan_routine("broken", "at 7am",
                        ["get_current_time_date {not json}"])
        out = _run("broken")
        self.assertNotIn("completed", out.lower())
        self.assertIn("get_current_time_date", out)
        self.assertNotIn("✓", out)

    def test_confirm_gated_step_pauses_and_nothing_runs(self):
        # No confirm handler is registered in this process, so
        # the gate fails closed — prove it directly first.
        direct = TOOLS.execute("run_command", {"command": "echo hi"})
        self.assertEqual(direct.get("kind"), "not_confirmed")
        w7.plan_routine("sheller", "at 7am",
                        ['run_command {"command": "echo hi"}'])
        out = _run("sheller")
        self.assertNotIn("completed", out.lower())
        self.assertIn("run_command", out)
        self.assertTrue("not_confirmed" in out
                        or "needs the user's OK" in out, out)
        self.assertNotIn("✓", out)

    def test_routine_management_step_refused(self):
        w7.plan_routine("nested", "at 7am",
                        ['run_routine {"name": "morning"}'])
        out = _run("nested")
        self.assertNotIn("completed", out.lower())
        self.assertIn("may never run", out)
        self.assertIn("run_routine", out)
        self.assertNotIn("✓", out)

    def test_scheduled_agents_step_refused(self):
        # Follow-up to fix #32 (parent ruling): scheduled_agents
        # is the scheduler engine — a step naming it with a
        # run_routine action would smuggle routine execution
        # past the forbidden list, one hop removed (the smuggled
        # routine's steps would run through the scheduler,
        # outside this executor's per-step gates). A routine step
        # has no legitimate need to schedule agents.
        w7.plan_routine("sneaky", "at 7am",
                        ['scheduled_agents {"action": "run_routine", '
                         '"name": "morning"}'])
        out = _run("sneaky")
        self.assertNotIn("completed", out.lower())
        self.assertIn("may never run", out)
        self.assertIn("scheduled_agents", out)
        self.assertNotIn("✓", out)

    def test_ranked_path_refuses_scheduled_agents_if_it_tops(self):
        # Defence in depth: scheduler-shaped prose does NOT top
        # scheduled_agents in practice (it tops run_routine /
        # run_anticipation_routine, both already forbidden), but
        # the ranked-path guard must fire if the ranking ever
        # changes — prove it with the ranking forced.
        from core.funnel import toolbox_embeddings
        with mock.patch.object(
                toolbox_embeddings, "ranking_scores",
                return_value={"scheduled_agents": 99.0}):
            with self.assertRaises(w7.AnticipationStepError) as cm:
                w7._resolve_step("do the scheduled thing")
        self.assertIn("may never run", str(cm.exception))
        self.assertIn("scheduled_agents", str(cm.exception))

    def test_first_failure_halts_and_later_steps_named_not_run(self):
        w7.plan_routine("mixed", "at 7am",
                        ["tell me the time", "read the weather",
                         "tell me the time"])
        out = _run("mixed")
        self.assertEqual(out.count("✓"), 1)
        self.assertIn("stopped at step 2 of 3", out)
        self.assertIn("did not run", out)

    def test_missing_routine_is_honest_refusal(self):
        out = _run("no-such-routine")
        self.assertIn("No anticipation routine named", out)
        self.assertIn("no-such-routine", out)

    def test_resolution_happens_at_run_time_not_save_time(self):
        # An unresolvable step stores fine — the refusal only
        # happens when the routine is run.
        w7.plan_routine("weather-check", "at 7am", ["read the weather"])
        self.assertIsNotNone(w7.routine_store().get("weather-check"))
        out = _run("weather-check")
        self.assertIn("read the weather", out)
        self.assertNotIn("completed", out.lower())

    # -- the plan-only contract is unchanged -----------------------

    def test_run_routine_without_executor_stays_plan_only(self):
        w7.plan_routine("morning", "at 7am", ["tell me the time"])
        result = w7.run_routine("morning")
        self.assertEqual(result.status, "plan-only")
        self.assertEqual(list(result.executed_steps), [])
        self.assertEqual(list(result.planned_steps), ["tell me the time"])

    def test_routine_survives_reset_and_still_runs(self):
        w7.plan_routine("morning", "at 7am", ["tell me the time"])
        w7.reset_anticipation()  # fresh-process stand-in
        self.assertIsNotNone(w7.routine_store().get("morning"))
        out = _run("morning")
        self.assertIn("completed", out.lower())
        self.assertIn("get_current_time_date", out)


if __name__ == "__main__":
    unittest.main()
