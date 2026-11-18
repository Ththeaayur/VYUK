"""B22 — sub-goal decomposition + re-planning (coordinator).

Lab leg only. The real-Windows field leg — a real complex goal
on his machine that the loop breaks down, runs, and re-plans
when the first plan fails — is OWED, not this batch.

Gap proved first (see core/brain/task_coordinator_execution.
run_task and planner_rungoal.run_goal): a task's steps had to
be declared up front — handle_turn handles only saved_plan /
routine, free text returns handled=False — and run_goal only
re-picks one action at a time. When the B10 goal check
returned not-met the task failed honestly and nothing
re-planned ("re-planning is B22's job", execution.py). No
code anywhere broke a free-text goal into sub-goals, or
planned a fresh step list after a not-met verdict.

What this batch builds (and this suite proves): ONE
decompose-and-re-plan capability on the coordinator, through
the SAME text brain the picker already uses (the
companion-session pair — never a new brain). A free-text goal
is decomposed into sub-goals, each into a small step list,
validated like any declared plan (StepDef.from_mapping +
plan-level validators + registry tool-name check), and run
as coordinator steps. A B10 not-met verdict re-plans (a
fresh step list for the remaining work) instead of failing —
bounded at 3 re-plans per task (the strike limit, one shared
budget, not a second counter). Each re-plan is recorded in
the receipt and the ledger (what was tried, what failed,
what the new plan is). A re-plan never re-runs a step that
already succeeded (the B21 step-run record stands), never
resets strikes, never opens a fresh budget window (B14),
and never re-plans a cancelled task (B20 — the user said
stop). A re-plan that hits an ambiguity asks (B15 — it
does not guess). D1 is still owed: NO typed free-form entry
point is wired here — run_decomposed_goal is invocable from
the existing typed-plan path only, and no voice path calls
it (Stage 1 hold — H1).
"""

import json
import os
import pathlib
import sys
import tempfile
import types as _types_mod
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
_PKGROOT = ROOT / ".pkgroot"
if _PKGROOT.is_dir() and str(_PKGROOT) not in sys.path:
    sys.path.insert(0, str(_PKGROOT))

# --- voice_assistant.* alias (mirrors test_startup_recovery) --------
# gui/pending_recovery.py imports voice_assistant.core.* at top
# level (the shipped install is laid out as the voice_assistant
# package; the lab tree is top-level core/ + gui/). The alias is
# installed at import time — before importing gui.pending_recovery
# — with the same snapshot discipline in tearDownModule.
_VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")}
_pkg = _types_mod.ModuleType("voice_assistant")
_pkg.__path__ = [str(ROOT)]
_core_alias = _types_mod.ModuleType("voice_assistant.core")
_core_alias.__path__ = [str(ROOT / "core")]
sys.modules["voice_assistant"] = _pkg
sys.modules["voice_assistant.core"] = _core_alias


def tearDownModule():
    global _VA_SNAPSHOT
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        del sys.modules[key]
    if _VA_SNAPSHOT is not None:
        sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None


from core.brain import task_coordinator as tc  # noqa: E402
from core.brain import task_decompose as td  # noqa: E402
from core.brain import task_ledger as tl  # noqa: E402
from core.brain.task_coordinator import StepDef  # noqa: E402
from core.memory.playbook_store import PlaybookStore  # noqa: E402
from gui.pending_recovery import (  # noqa: E402
    replan_line,
    recovery_offer_line,
)


# ---------------------------------------------------------------------------
# fakes (mirroring tests/test_goal_verification.py + test_midtask_questions.py)
# ---------------------------------------------------------------------------

# Read-only, registry-declared tools (the plan steps use real
# tool names — the brain's tool-name check runs against the
# real registry, so a fake name like "a" would fail the plan).
TOOL_A = "battery_info"
TOOL_B = "bluetooth_devices"
TOOL_C = "ascii_art"

GOAL = "text 'report ready' present"


def _ok(**kw):
    def _run(tool, args):
        _run.calls.append((tool, dict(args)))
        return {"status": "success", "kind": None, "result": "done"}
    _run.calls = []
    return _run


def _fail(message="simulated failure"):
    def _run(tool, args):
        _run.calls.append((tool, dict(args)))
        return {"status": "failure", "message": message}
    _run.calls = []
    return _run


class _ScriptExecutor:
    """Per-tool scripted results; records every call."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def __call__(self, tool, args):
        self.calls.append(tool)
        results = self.script.get(tool)
        if not results:
            return {"status": "success", "result": "done"}
        if len(results) > 1:
            return results.pop(0)
        return results[0]


class _RecordingVerifier:
    """Per-expect scripted verdicts; records every full call."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []  # (expect_text, before, after, result_text)

    def __call__(self, expect_text, before, after, result_text):
        self.calls.append((expect_text, before, after, result_text))
        verdicts = self.script.get(expect_text)
        if not verdicts:
            return ("unverifiable", "no script")
        if len(verdicts) > 1:
            return verdicts.pop(0)
        return verdicts[0]


class _Snapshot:
    def __call__(self):
        return {"fg": "fake", "windows": ["fake"], "texts": []}


class _ScriptedBrain:
    """A fake text brain: returns plan texts in order (last repeats)."""

    def __init__(self, plans):
        self.plans = list(plans)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(str(prompt or ""))
        if len(self.plans) > 1:
            return self.plans.pop(0)
        return self.plans[0] if self.plans else ""


class _CancellingVerifier(_RecordingVerifier):
    """A verifier that cancels the task during the goal check.

    The goal check is the one window where the task reads
    "completed" transiently — cancel_task must still land
    there (B22 marker), and the re-plan after it must not
    run. The cancel is issued through the public API, like
    the GUI Stop button would.
    """

    def __init__(self, script, coord_ref):
        super().__init__(script)
        self.coord_ref = coord_ref

    def __call__(self, expect_text, before, after, result_text):
        out = super().__call__(expect_text, before, after, result_text)
        if expect_text == GOAL:
            coord = self.coord_ref.get("coord")
            task = self.coord_ref.get("task")
            if coord is not None and task is not None:
                self.coord_ref["cancelled"] = coord.cancel_task(task.id)
        return out


def _steps(*mappings):
    return [StepDef.from_mapping(m) for m in mappings]


def _plan_json(subgoals):
    return json.dumps({"subgoals": subgoals})


DECOMPOSE_JSON = _plan_json([
    {"name": "Check battery",
     "steps": [{"tool": TOOL_A, "label": "check battery"}]},
    {"name": "List devices",
     "steps": [{"tool": TOOL_B, "label": "list devices"}]},
])

REPLAN_JSON = _plan_json([
    {"name": "Try another check",
     "steps": [{"tool": TOOL_C, "label": "draw summary"}]},
])

REPLAN_GOTO_JSON = _plan_json([
    {"name": "Retry the failing check",
     "steps": [{"tool": TOOL_A, "label": "retry check",
                "on_fail": {"goto": 2}},
               {"tool": TOOL_B, "label": "list devices instead"}]},
])

REPLAN_ASK_JSON = _plan_json([
    {"name": "Ask where the summary goes",
     "steps": [{"tool": TOOL_C, "label": "draw summary",
                "ask": {"question": "Which file should the summary go in?",
                        "options": ["a.txt", "b.txt"],
                        "answer_key": "file"}}]},
])


def _coord(tmp, executor=None, verifier=None, brains=None):
    ledger = tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")
    store = PlaybookStore(path=pathlib.Path(tmp) / "playbooks.json")
    coord = tc.TurnCoordinator(
        executor=executor or _ok(), verifier=verifier,
        snapshot=_Snapshot(), ledger=ledger, playbook=store,
        second_opinion_brains=brains)
    return coord, ledger, store


def _run_decomposed(tmp, goal, verifier_script, brain_plans,
                    executor=None):
    """Run a free-text goal with a scripted brain + goal verdicts.

    Returns (receipt, coord, ledger, store, verifier). The
    coordinator is constructed with the fakes (the executor
    and verifier are constructor-only — self._executor /
    self._verifier — never reassigned after).
    """
    verifier = _RecordingVerifier(verifier_script)
    coord, ledger, store = _coord(
        tmp, executor=executor or _ok(), verifier=verifier,
        brains=(_ScriptedBrain(brain_plans), _ScriptedBrain([""])))
    receipt = coord.run_decomposed_goal(goal)
    return receipt, coord, ledger, store, verifier


# ---------------------------------------------------------------------------
# 1. task_decompose: the plan shape is the contract
# ---------------------------------------------------------------------------

class DecomposeShapeTests(unittest.TestCase):
    def test_parse_valid_plan(self):
        plan, error = td.parse_plan_text(DECOMPOSE_JSON)
        self.assertEqual(error, "")
        self.assertEqual(td.plan_subgoal_names(plan),
                         ["Check battery", "List devices"])
        self.assertEqual(
            [m["tool"] for m in td.plan_step_mappings(plan)],
            [TOOL_A, TOOL_B])

    def test_parse_rejects_malformed(self):
        for bad in (
                "not json at all",
                json.dumps({"steps": [{"tool": TOOL_A}]}),
                json.dumps({"subgoals": []}),
                json.dumps({"subgoals": [{"name": "x", "steps": []}]}),
                json.dumps({"subgoals": [{"name": "x",
                                          "steps": [{"label": "no tool"}]}]}),
                json.dumps({"subgoals": [{"name": "x", "steps": [
                    {"tool": TOOL_A},
                    {"tool": TOOL_A, "on_fail": {"goto": 9}}]}]}),
        ):
            plan, error = td.parse_plan_text(bad)
            self.assertIsNone(plan, bad)
            self.assertTrue(error, bad)
        # Over the step budget: 26 one-step sub-goals.
        many = {"subgoals": [{"name": f"s{i}",
                              "steps": [{"tool": TOOL_A}]}
                             for i in range(26)]}
        plan, error = td.parse_plan_text(json.dumps(many))
        self.assertIsNone(plan)
        self.assertIn("25", error)

    def test_parse_finds_json_inside_prose(self):
        plan, error = td.parse_plan_text(
            "Here is the plan:\n" + DECOMPOSE_JSON + "\nGood luck.")
        self.assertEqual(error, "")
        self.assertEqual(len(td.plan_step_mappings(plan)), 2)

    def test_decompose_prompt_names_goal_and_candidates(self):
        prompt = td.build_decompose_prompt(GOAL, [TOOL_A, TOOL_B])
        self.assertIn(GOAL, prompt)
        self.assertIn(TOOL_A, prompt)
        self.assertIn("subgoals", prompt)

    def test_replan_prompt_names_tried_and_reason(self):
        prompt = td.build_replan_prompt(
            GOAL,
            [{"label": "check battery", "tool": TOOL_A,
              "status": "success"}],
            "text 'report ready' is not present",
            [TOOL_C])
        self.assertIn(GOAL, prompt)
        self.assertIn("check battery", prompt)
        self.assertIn("text 'report ready' is not present", prompt)
        self.assertIn(TOOL_C, prompt)


# ---------------------------------------------------------------------------
# 2. coordinator: free-text goal decomposes and runs
# ---------------------------------------------------------------------------

class DecomposeGoalTests(unittest.TestCase):
    def test_free_text_goal_decomposes_and_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            receipt, coord, ledger, _store, _verifier = _run_decomposed(
                tmp, GOAL, {GOAL: [("met", "goal state observed")]},
                [DECOMPOSE_JSON], executor=executor)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(receipt.goal_verdict["verdict"], "met")
            # The decomposition is in the receipt.
            decomposed = receipt.as_dict()["decomposition"]
            self.assertEqual(decomposed["subgoals"],
                             ["Check battery", "List devices"])
            self.assertEqual(decomposed["brain"], "cloud")
            self.assertEqual(decomposed["steps"], 2)
            self.assertEqual(receipt.as_dict()["replans"], [])
            # The steps ran as coordinator steps, in plan order.
            self.assertEqual([c[0] for c in executor.calls],
                             [TOOL_A, TOOL_B])
            # The decomposition is in the ledger too.
            record = ledger.get(receipt.task_id)
            self.assertEqual(record["decomposition"]["subgoals"],
                             ["Check battery", "List devices"])
            self.assertEqual(record["replans"], [])
            # The brain was asked for a decomposition (not a
            # one-step pick): the prompt names the goal.
            brain = coord._second_opinion_brains[0]
            self.assertEqual(len(brain.prompts), 1)
            self.assertIn(GOAL, brain.prompts[0])

    def test_no_brain_fails_honestly(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            coord, _ledger, _store = _coord(tmp, executor=executor)
            coord._second_opinion_brains = None
            receipt = coord.run_decomposed_goal(GOAL)
            self.assertEqual(receipt.state, "failed")
            self.assertIn("decompose", receipt.reason.lower())
            self.assertEqual(executor.calls, [])

    def test_malformed_plan_fails_honestly(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            receipt, _coord2, _ledger, _store, _verifier = _run_decomposed(
                tmp, GOAL, {}, ["not json at all"], executor=executor)
            self.assertEqual(receipt.state, "failed")
            self.assertIn("unusable", receipt.reason)
            self.assertEqual(executor.calls, [])

    def test_unknown_tool_plan_fails_honestly(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            bad = _plan_json([
                {"name": "Do the impossible",
                 "steps": [{"tool": "definitely_not_a_tool",
                            "label": "impossible"}]},
            ])
            receipt, _coord2, _ledger, _store, _verifier = _run_decomposed(
                tmp, GOAL, {}, [bad], executor=executor)
            self.assertEqual(receipt.state, "failed")
            self.assertIn("definitely_not_a_tool", receipt.reason)
            self.assertEqual(executor.calls, [])

    def test_local_brain_fallback_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            cloud = _ScriptedBrain([""])
            local = _ScriptedBrain([DECOMPOSE_JSON])
            coord, _ledger, _store = _coord(
                tmp, verifier=_RecordingVerifier(
                    {GOAL: [("met", "goal state observed")]}),
                brains=(cloud, local))
            receipt = coord.run_decomposed_goal(GOAL)
            self.assertEqual(receipt.state, "completed")
            # The cloud leg was asked first and answered
            # nothing; the local leg planned, and the record
            # names it — the fallback is recorded, never
            # silent.
            self.assertEqual(len(cloud.prompts), 1)
            self.assertEqual(len(local.prompts), 1)
            self.assertEqual(
                receipt.as_dict()["decomposition"]["brain"], "local")
            # B14: the local leg is recorded as a local call
            # (no cloud call was made — the cloud leg
            # answered nothing usable, so nothing is recorded
            # for it).
            self.assertEqual(receipt.as_dict()["spend"]["local_calls"], 1)
            self.assertEqual(receipt.as_dict()["spend"]["cloud_calls"], [])


# ---------------------------------------------------------------------------
# 3. coordinator: a not-met verdict re-plans instead of failing
# ---------------------------------------------------------------------------

class ReplanTests(unittest.TestCase):
    def test_not_met_triggers_replan_instead_of_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            receipt, coord, ledger, _store, _verifier = _run_decomposed(
                tmp, GOAL,
                {GOAL: [("not-met", "text 'report ready' is not present"),
                        ("met", "goal state observed")]},
                [DECOMPOSE_JSON, REPLAN_JSON], executor=executor)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(receipt.goal_verdict["verdict"], "met")
            # ONE re-plan, recorded in full.
            replans = receipt.as_dict()["replans"]
            self.assertEqual(len(replans), 1)
            event = replans[0]
            self.assertEqual(event["attempt"], 1)
            self.assertEqual(event["tried"],
                             ["check battery", "list devices"])
            self.assertEqual(event["failed_reason"],
                             "text 'report ready' is not present")
            self.assertEqual(event["subgoals"], ["Try another check"])
            self.assertEqual(event["steps"], 1)
            self.assertEqual(event["first_step_index"], 2)
            self.assertEqual(event["brain"], "cloud")
            # The ledger carries the same record.
            record = ledger.get(receipt.task_id)
            self.assertEqual(len(record["replans"]), 1)
            self.assertEqual(record["replans"][0]["attempt"], 1)
            # All three steps ran, in order: the initial plan's
            # two, then the re-plan's one.
            self.assertEqual([c[0] for c in executor.calls],
                             [TOOL_A, TOOL_B, TOOL_C])

    def test_replan_budget_three_then_honest_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            # Declared one-step plan; the brain answers every
            # re-plan with the same one-step plan (the last
            # scripted plan repeats); the goal is never met.
            verifier = _RecordingVerifier({
                GOAL: [("not-met", "still not ready"),
                       ("not-met", "still not ready"),
                       ("not-met", "still not ready"),
                       ("not-met", "still not ready")]})
            coord, ledger, _store = _coord(
                tmp, executor=executor, verifier=verifier,
                brains=(_ScriptedBrain([REPLAN_JSON]),
                        _ScriptedBrain([""])))
            task = coord.new_task(
                GOAL, "saved_plan",
                _steps({"tool": TOOL_A, "label": "check battery"}))
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "failed")
            self.assertEqual(receipt.goal_verdict["verdict"], "not-met")
            # Exactly three re-plans, then an honest failure
            # that names the spent budget.
            self.assertEqual(len(receipt.as_dict()["replans"]), 3)
            self.assertIn("re-plan budget spent (3 re-plans)",
                          receipt.reason)
            self.assertIn(GOAL, receipt.reason)
            # Four runs of the plan (initial + 3 re-plans).
            self.assertEqual(len(executor.calls), 4)
            record = ledger.get(task.id)
            self.assertEqual(len(record["replans"]), 3)

    def test_replan_never_reruns_succeeded_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            receipt, coord, ledger, _store, _verifier = _run_decomposed(
                tmp, GOAL,
                {GOAL: [("not-met", "text 'report ready' is not present"),
                        ("met", "goal state observed")]},
                [DECOMPOSE_JSON, REPLAN_JSON], executor=executor)
            self.assertEqual(receipt.state, "completed")
            # Each tool ran exactly once — the re-plan ran only
            # its own new step, never the succeeded ones.
            tools = [c[0] for c in executor.calls]
            self.assertEqual(tools, [TOOL_A, TOOL_B, TOOL_C])
            self.assertEqual(tools.count(TOOL_A), 1)
            # The B21 step-run record stands: step 0 has
            # exactly one attempt, recorded success.
            task = coord.get_task(receipt.task_id)
            runs_0 = [r for r in task._step_runs
                      if r["step_index"] == 0]
            self.assertEqual(len(runs_0), 1)
            self.assertEqual(runs_0[0]["status"], "success")
            # Outcomes stay positionally aligned with steps.
            self.assertEqual(len(task.outcomes), 3)
            self.assertEqual([o.status for o in task.outcomes],
                             ["success", "success", "success"])

    def test_replan_verifies_only_new_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            initial = _plan_json([
                {"name": "Check battery",
                 "steps": [{"tool": TOOL_A, "label": "check battery",
                            "expect": "text 'battery checked' present"}]},
            ])
            replan = _plan_json([
                {"name": "Draw the summary",
                 "steps": [{"tool": TOOL_C, "label": "draw summary",
                            "expect": "text 'summary drawn' present"}]},
            ])
            verifier = _RecordingVerifier({
                "text 'battery checked' present": [("met", "a ok")],
                "text 'summary drawn' present": [("met", "b ok")],
                GOAL: [("not-met", "first plan missed"),
                       ("met", "goal state observed")]})
            coord, _ledger, _store = _coord(
                tmp, executor=executor, verifier=verifier,
                brains=(_ScriptedBrain([initial, replan]),
                        _ScriptedBrain([""])))
            receipt = coord.run_decomposed_goal(GOAL)
            self.assertEqual(receipt.state, "completed")
            # The first pass verified the old step once; the
            # re-plan pass verified ONLY the new step — the
            # old step's expectation was never checked again
            # (re-verifying it could retry a succeeded step).
            a_calls = [c for c in verifier.calls
                       if c[0] == "text 'battery checked' present"]
            b_calls = [c for c in verifier.calls
                       if c[0] == "text 'summary drawn' present"]
            self.assertEqual(len(a_calls), 1)
            self.assertEqual(len(b_calls), 1)
            # Both verdicts stand in the task's verification.
            task = coord.get_task(receipt.task_id)
            self.assertEqual(
                [v["verdict"] for v in task.verification],
                ["met", "met"])

    def test_cancelled_task_never_replanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            ref = {}
            verifier = _CancellingVerifier(
                {GOAL: [("not-met", "text 'report ready' is not present")]},
                ref)
            coord, _ledger, _store = _coord(
                tmp, executor=executor, verifier=verifier,
                brains=(_ScriptedBrain([DECOMPOSE_JSON, REPLAN_JSON]),
                        _ScriptedBrain([""])))
            task = coord.new_task(
                GOAL, "multi_step", [],
                turn_text=GOAL)
            ref["coord"] = coord
            ref["task"] = task
            # Decompose first (the brain path), then run: the
            # goal check cancels the task mid-check through
            # the public API, and the re-plan must not run.
            error = coord._decompose_goal(task)
            self.assertEqual(error, "")
            receipt = coord.run_task(task)
            self.assertTrue(ref["cancelled"],
                            "cancel_task refused during the goal check")
            self.assertEqual(receipt.state, "cancelled")
            self.assertEqual(receipt.as_dict()["replans"], [])
            # Only the initial plan's steps ran — no re-plan
            # step was ever appended or run.
            self.assertEqual([c[0] for c in executor.calls],
                             [TOOL_A, TOOL_B])
            self.assertEqual(len(task.steps), 2)

    def test_replan_shares_budget_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            receipt, coord, ledger, _store, _verifier = _run_decomposed(
                tmp, GOAL,
                {GOAL: [("not-met", "text 'report ready' is not present"),
                        ("met", "goal state observed")]},
                [DECOMPOSE_JSON, REPLAN_JSON], executor=executor)
            self.assertEqual(receipt.state, "completed")
            task = coord.get_task(receipt.task_id)
            # ONE budget window for the whole task (initial
            # plan + re-plan) — the re-plan opened no fresh
            # window, so its steps were charged to the same
            # one. (B14: begin_window only opens when none
            # is open.)
            self.assertEqual(task._budget_windows, 1)
            record = ledger.get(task.id)
            self.assertEqual(record["budget_windows"], 1)
            self.assertEqual(
                receipt.as_dict()["spend"]["steps_run"], 3)

    def test_replan_ambiguity_asks_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            receipt, coord, ledger, _store, _verifier = _run_decomposed(
                tmp, GOAL,
                {GOAL: [("not-met", "text 'report ready' is not present"),
                        ("met", "goal state observed")]},
                [DECOMPOSE_JSON, REPLAN_ASK_JSON], executor=executor)
            # The re-plan's step asks — the task pauses, and
            # the step never ran (no guess).
            self.assertEqual(receipt.state, "paused")
            task = coord.get_task(receipt.task_id)
            self.assertIsNotNone(task._pending_question)
            self.assertEqual(
                task._pending_question["question"],
                "Which file should the summary go in?")
            self.assertEqual([c[0] for c in executor.calls],
                             [TOOL_A, TOOL_B])
            # The re-plan itself is recorded even though the
            # task paused mid-re-plan.
            self.assertEqual(len(receipt.as_dict()["replans"]), 1)
            # Answering resumes through the normal run loop
            # and completes (the goal check now says met).
            receipt2 = coord.resolve_question(task.id, "a.txt")
            self.assertIsNotNone(receipt2)
            self.assertEqual(receipt2.state, "completed")
            self.assertEqual([c[0] for c in executor.calls],
                             [TOOL_A, TOOL_B, TOOL_C])
            self.assertEqual(executor.calls[2][1].get("file"), "a.txt")

    def test_replan_goto_is_rebased_to_merged_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ScriptExecutor(
                {TOOL_A: [{"status": "failure",
                           "message": "retry check broke"}]})
            verifier = _RecordingVerifier({
                GOAL: [("not-met", "first plan missed"),
                       ("met", "goal state observed")]})
            # The initial plan is ONE step on a different tool
            # (TOOL_C) — the failing tool (TOOL_A) belongs to
            # the re-plan alone, so the first plan completes
            # and the re-plan's failing step is the one that
            # jumps.
            initial = _plan_json([
                {"name": "Draw first",
                 "steps": [{"tool": TOOL_C, "label": "draw first"}]},
            ])
            coord, _ledger, _store = _coord(
                tmp, executor=executor, verifier=verifier,
                brains=(_ScriptedBrain([initial,
                                        REPLAN_GOTO_JSON]),
                        _ScriptedBrain([""])))
            receipt = coord.run_decomposed_goal(GOAL)
            self.assertEqual(receipt.state, "completed")
            # The re-plan's first step failed and jumped to
            # the re-plan's second step (merged index 2, not
            # plan index 1) — the jump landed inside the
            # re-plan, never back into the succeeded steps.
            self.assertEqual(executor.calls,
                             [TOOL_C, TOOL_A, TOOL_B])
            task = coord.get_task(receipt.task_id)
            self.assertEqual(task.steps[1].goto, 2)
            self.assertEqual(task.steps[2].goto, None)

    def test_merged_group_too_big_rolls_replan_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            initial = _plan_json([
                {"name": "Pair up",
                 "steps": [{"tool": TOOL_A, "label": "first",
                            "parallel": "g"},
                           {"tool": TOOL_B, "label": "second",
                            "parallel": "g"}]},
            ])
            # Three more "g" steps: merged with the initial
            # two they form a group of five — over the max
            # of four — so the re-plan must be refused whole.
            replan = _plan_json([
                {"name": "Too many in parallel",
                 "steps": [{"tool": TOOL_C, "label": "third",
                            "parallel": "g"},
                           {"tool": TOOL_A, "label": "fourth",
                            "parallel": "g"},
                           {"tool": TOOL_B, "label": "fifth",
                            "parallel": "g"}]},
            ])
            verifier = _RecordingVerifier({
                GOAL: [("not-met", "first plan missed"),
                       ("met", "goal state observed")]})
            coord, _ledger, _store = _coord(
                tmp, executor=executor, verifier=verifier,
                brains=(_ScriptedBrain([initial, replan]),
                        _ScriptedBrain([""])))
            receipt = coord.run_decomposed_goal(GOAL)
            # The re-plan failed validation, so the task fails
            # honestly naming both the unmet goal and the
            # re-plan failure — and no step was appended.
            self.assertEqual(receipt.state, "failed")
            self.assertIn("goal not met", receipt.reason)
            self.assertIn("re-plan failed", receipt.reason)
            self.assertEqual(receipt.as_dict()["replans"], [])
            task = coord.get_task(receipt.task_id)
            self.assertEqual(len(task.steps), 2)
            self.assertEqual([c[0] for c in executor.calls],
                             [TOOL_A, TOOL_B])

    def test_replan_lesson_names_the_replan(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            receipt, coord, ledger, store, _verifier = _run_decomposed(
                tmp, GOAL,
                {GOAL: [("not-met", "text 'report ready' is not present"),
                        ("met", "goal state observed")]},
                [DECOMPOSE_JSON, REPLAN_JSON], executor=executor)
            self.assertEqual(receipt.state, "completed")
            entry = store._entries.get(GOAL)
            self.assertIsNotNone(entry, "no lesson written")
            self.assertTrue(entry.proven)
            # The re-planned step carries its attempt in its
            # description — a lesson that only works after a
            # re-plan says so.
            self.assertEqual(
                [s["description"] for s in entry.steps],
                ["check battery", "list devices",
                 "draw summary (re-plan 1)"])

    def test_replan_history_survives_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            receipt, coord, ledger, _store, _verifier = _run_decomposed(
                tmp, GOAL,
                {GOAL: [("not-met", "text 'report ready' is not present"),
                        ("met", "goal state observed")]},
                [DECOMPOSE_JSON, REPLAN_ASK_JSON], executor=executor)
            self.assertEqual(receipt.state, "paused")
            # Crash/restart: rebuild the paused record — the
            # re-plan history restores, so the resumed task
            # counts re-plans from history, never fresh ones.
            record = ledger.get(receipt.task_id)
            coord2, _ledger2, _store2 = _coord(
                tmp, executor=executor,
                verifier=_RecordingVerifier(
                    {GOAL: [("met", "goal state observed")]}),
                brains=(_ScriptedBrain([REPLAN_JSON]),
                        _ScriptedBrain([""])))
            receipt2 = coord2.resume_ledger_task(record, ledger=ledger)
            self.assertIsNotNone(receipt2)
            self.assertEqual(receipt2.state, "paused")
            rebuilt = coord2.get_task(receipt.task_id)
            self.assertEqual(len(rebuilt._replans), 1)
            self.assertEqual(rebuilt._replans[0]["attempt"], 1)
            self.assertEqual(rebuilt._decomposition["subgoals"],
                             ["Check battery", "List devices"])
            self.assertEqual(rebuilt._replan_base, 0)
            # Resume the question — the task completes, and
            # the resumed run grants no fresh re-plan budget
            # (the history already holds one attempt).
            receipt3 = coord2.resolve_question(rebuilt.id, "a.txt")
            self.assertIsNotNone(receipt3)
            self.assertEqual(receipt3.state, "completed")
            self.assertEqual(len(receipt3.as_dict()["replans"]), 1)


# ---------------------------------------------------------------------------
# 4. recovery offer: a task interrupted mid-re-plan names it
# ---------------------------------------------------------------------------

class RecoveryOfferTests(unittest.TestCase):
    def test_replan_line_names_history(self):
        self.assertEqual(replan_line({}), "")
        self.assertEqual(replan_line({"replans": []}), "")
        line = replan_line({"replans": [{
            "attempt": 1, "subgoals": ["Try another check"],
            "steps": 1, "first_step_index": 2, "brain": "cloud"}]})
        self.assertIn("re-planned 1 time(s)", line)
        self.assertIn("Try another check", line)

    def test_recovery_offer_line_carries_replan(self):
        summary = {
            "title": GOAL, "state": "paused", "completed": 2,
            "remaining_labels": ["draw summary"],
            "reason": "paused for a question",
            "spend": {"elapsed_s": 3.0, "steps_run": 2, "executions": 2,
                      "cloud_calls": [], "local_calls": 0,
                      "cost_usd": 0.0, "cost_known": True},
            "replans": [{"attempt": 1,
                         "subgoals": ["Try another check"],
                         "steps": 1, "first_step_index": 2,
                         "brain": "cloud"}],
            "decomposition": {"subgoals": ["Check battery"],
                              "brain": "cloud", "steps": 2},
        }
        line = recovery_offer_line("task", summary)
        # _cap is a length cap, not a capitalizer — the
        # fragment reads lower-case, like the other parts.
        self.assertIn("re-planned 1 time(s)", line)
        self.assertIn("Try another check", line)
        # A summary without re-plans names none.
        plain = dict(summary)
        plain["replans"] = []
        self.assertNotIn("re-planned",
                         recovery_offer_line("task", plain))


if __name__ == "__main__":
    unittest.main()
