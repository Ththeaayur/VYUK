"""B23 — approve-plan-once coverage (L19 mitigation, Fix 8 extension).

Fix 8's plan approval covered only the coordinator's declared-step
path (positional: begin_step_dispatch at the task's current step
index). Two execution sites never consulted it, so a long voice
task still popped at every step there:
  * run_goal's picked actions (core/brain/planner_rungoal.py) —
    the path text goals AND voice goals take (the voice bridge
    hands the goal to the text loop, which picks actions one at
    a time and executes them through _execute_tool);
  * the legacy run_routine loop (core/brain/planner_routines.py)
    — the nesting fallback of run_routine_as_task and the GUI
    "run routine" button.
ONE design: match by CONTENT — plan_approval.arm_for_call tries
each approved step's record through begin_step_dispatch itself
(tool + full args digest), so a picked action or legacy step
identical to an approved one arms the channel and the funnel's
covering_call skips the popup; anything else asks as today. The
coordinator's _run_step keeps its positional check first and
falls back to content (a B22 re-plan appends steps at NEW
indices, so a re-used approved step would otherwise ask).
C-14 stands: the approval itself is GUI-only; only its coverage
travels. No spoken approval is invented anywhere in this suite.

Lab leg only. The real-Windows field leg — a real voice task on
his machine where the plan he already approved covers the steps
without a fresh popup at each one — is OWED, not this batch.

Named residual (honest, not hidden): the voice-goal bridge
(run_bridged_voice_goal) owns a coordinator task with EMPTY
steps and no plan dialog, so no approval exists for a voice
goal today — the consult machinery is proven here at run_goal
(the bridge's execution site) with the context such a task
would carry, and threading (task.id, steps) through hand_off
is a one-kwarg follow-up once a voice-goal plan flow (decompose
-> dialog -> approve) exists. The voice ROUTINE path is fully
wired in production by this batch: the coordinator path was
already covered, and the nesting fallback now shows the same
plan dialog once and hands its approval to the legacy loop.
"""

import asyncio
import contextlib
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import confirm_gate  # noqa: E402
from core import injection  # noqa: E402
from core import permission_grants as pg  # noqa: E402
from core import plan_approval as pa  # noqa: E402
from core.brain import planner_rungoal as rg  # noqa: E402
from core.brain import planner_routines as pr  # noqa: E402
from core.brain import task_coordinator as tc  # noqa: E402
from core.brain import task_ledger as tl  # noqa: E402
from core.brain.task_coordinator_steps import StepDef  # noqa: E402

GOAL = "write the note"


class _DataGuard:
    """Restore data/ after funnel executes (consumers-batch pattern)."""

    def _scan(self):
        found = {}
        for path in sorted((ROOT / "data").rglob("*")):
            if path.is_file():
                try:
                    found[path.relative_to(ROOT / "data")] = \
                        path.read_bytes()
                except OSError:
                    pass
        return found

    def __enter__(self):
        self._saved = self._scan()
        return self

    def __exit__(self, *exc):
        current = self._scan()
        for rel in current:
            if rel not in self._saved:
                try:
                    ((ROOT / "data") / rel).unlink()
                except OSError:
                    pass
        for rel, data in self._saved.items():
            path = (ROOT / "data") / rel
            try:
                if not path.exists() or path.read_bytes() != data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            except OSError:
                pass
        return False


class _FunnelBase(unittest.TestCase):
    """Real funnel + fake confirm handler (Fix 8 FunnelPlanTests)."""

    def setUp(self):
        from core.config import config as app_config
        from core.funnel import tools_gate as tools_mod
        from core.funnel.tools_registry import tools
        self._tools = tools
        self._tools_mod = tools_mod
        self._tmp = tempfile.TemporaryDirectory()
        pg.set_path_override(
            pathlib.Path(self._tmp.name) / "permission_grants.json")
        self._guard = _DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        pa.clear_all()
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False

    def tearDown(self):
        from core.config import config as app_config
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        pa.clear_all()
        pa.register_plan_handler(None)
        self._tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()
        pg.clear_path_override()
        self._tmp.cleanup()

    def _write_step(self, dest, content="hello plan"):
        return StepDef.from_mapping(
            {"tool": "write_file",
             "args": {"filepath": str(dest), "content": content}})

    def _deny_handler(self, calls):
        confirm_gate.register_handler(
            lambda n, s: calls.append(n) or False)


# ---------------------------------------------------------------------------
# 1. arm_for_call: the content match itself
# ---------------------------------------------------------------------------

class ArmForCallTests(_FunnelBase):
    """Content matching needs the funnel harness: is_plan_approvable
    first asks the funnel's _needs_confirmation, which depends on
    the session-provenance setup (provenance "user", lock off).
    A bare TestCase leaves the default provenance in place and
    approve_plan refuses every index."""

    def setUp(self):
        super().setUp()

    def _plan(self, task_id):
        # write_file to a FRESH destination is confirm-gated and
        # plan-approvable (the kind Fix 8's own funnel tests
        # approve). UI kinds (click_element / mouse_control)
        # are NOT: _needs_confirmation returns False for them
        # under user provenance, so is_plan_approvable refuses
        # them as "runs without asking" — there is nothing to
        # cover there, and arm_for_call would have nothing to
        # match. Two steps so the content match can be proved
        # at a non-zero index.
        dest_a = pathlib.Path(self._tmp.name) / "arm_a.txt"
        dest_b = pathlib.Path(self._tmp.name) / "arm_b.txt"
        steps = [
            self._write_step(dest_a, content="a"),
            self._write_step(dest_b, content="b"),
        ]
        recorded = pa.approve_plan(task_id, steps, [0, 1])
        self.assertEqual(recorded["approved"], [0, 1])
        return steps, dest_a, dest_b

    def test_matches_by_content_regardless_of_position(self):
        steps, dest_a, dest_b = self._plan("task-1")
        # The call matches step 1's content (write B) —
        # armed at index 1, not at the "current" step.
        token = pa.arm_for_call(
            "task-1", steps, "write_file",
            {"filepath": str(dest_b), "content": "b"})
        self.assertIsNotNone(token)
        try:
            cur = pa.current()
            self.assertEqual(cur["tool"], "write_file")
            self.assertEqual(cur["index"], 1)
        finally:
            pa.end_step_dispatch(token)
        self.assertIsNone(pa.current())

    def test_no_match_when_args_differ(self):
        steps, dest_a, dest_b = self._plan("task-2")
        self.assertIsNone(pa.arm_for_call(
            "task-2", steps, "write_file",
            {"filepath": str(dest_b), "content": "different"}))
        self.assertIsNone(pa.arm_for_call(
            "task-2", steps, "write_file",
            {"filepath": str(dest_a), "content": "b"}))

    def test_no_match_without_an_approval(self):
        steps, dest_a, dest_b = self._plan("task-3")
        # A different task id has no record: nothing is covered.
        self.assertIsNone(pa.arm_for_call(
            "task-other", steps, "write_file",
            {"filepath": str(dest_a), "content": "a"}))
        # And an unapproved index never matches either.
        pa.clear_all()
        self.assertIsNone(pa.arm_for_call(
            "task-3", steps, "write_file",
            {"filepath": str(dest_a), "content": "a"}))

    def test_empty_task_id_and_none_steps_never_match(self):
        steps, dest_a, dest_b = self._plan("task-4")
        self.assertIsNone(pa.arm_for_call(
            "", steps, "write_file",
            {"filepath": str(dest_a), "content": "a"}))
        self.assertIsNone(pa.arm_for_call(
            "task-4", [None, None], "write_file",
            {"filepath": str(dest_a), "content": "a"}))
        self.assertIsNone(pa.arm_for_call(
            "task-4", None, "write_file",
            {"filepath": str(dest_a), "content": "a"}))

    def test_excluded_leg_is_never_approved_so_never_arms(self):
        steps = [
            StepDef.from_mapping(
                {"tool": "send_email",
                 "args": {"to": "a@b.c", "subject": "s", "body": "b"}}),
        ]
        recorded = pa.approve_plan("task-5", steps, [0])
        self.assertEqual(recorded["approved"], [])
        self.assertEqual(len(recorded["refused"]), 1)
        self.assertIsNone(pa.arm_for_call(
            "task-5", steps, "send_email",
            {"to": "a@b.c", "subject": "s", "body": "b"}))


# ---------------------------------------------------------------------------
# 2. run_goal through the REAL funnel: popup observed, not assumed
# ---------------------------------------------------------------------------

class _ChangingSnapshot:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return {"fg": "fake", "n": self.n, "windows": [f"fake"],
                "texts": []}


class RungoalCoverageTests(_FunnelBase):
    def _run_goal(self, dest, content, plan_approval=None):
        calls = []
        picked = []

        async def pick(goal, observation, history):
            # First pick: the action under test. Any later pick:
            # done — a covered action that ran must not be
            # picked again (the new-file leg would rightly
            # refuse its second run, which is not what this
            # test is about).
            if picked:
                return {"done": "note written"}
            picked.append(1)
            return {"tool": "write_file",
                    "args": {"filepath": str(dest), "content": content}}

        async def obs():
            return _ChangingSnapshot()()

        def verify(expect_text, before, after, result_text):
            return ("met", "goal state observed")

        self._deny_handler(calls)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = tl.TaskLedger(
                path=pathlib.Path(tmp) / "ledger.json")
            try:
                result = asyncio.run(rg.run_goal(
                    GOAL, pick_action=pick, observe=obs,
                    verify_expect=verify, ledger=ledger,
                    plan_approval=plan_approval))
            finally:
                confirm_gate.clear_handler()
        return result, calls

    def test_covered_picked_action_runs_without_a_popup(self):
        dest = pathlib.Path(self._tmp.name) / "goal_note.txt"
        step = self._write_step(dest)
        recorded = pa.approve_plan("goal-task", [step], [0])
        self.assertEqual(recorded["approved"], [0])
        result, calls = self._run_goal(
            dest, "hello plan", plan_approval=("goal-task", [step]))
        self.assertEqual(calls, [])
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.read_text(encoding="utf-8"), "hello plan")
        self.assertIn("Goal complete", result)

    def test_uncovered_picked_action_pops_and_denies(self):
        dest = pathlib.Path(self._tmp.name) / "goal_note2.txt"
        result, calls = self._run_goal(dest, "hello plan")
        self.assertEqual(calls, ["write_file"])
        self.assertFalse(dest.exists())
        self.assertNotIn("Goal complete", result)

    def test_digest_mismatch_pops(self):
        # Approved for content "hello plan"; the picker chose
        # different content — the digest is load-bearing, so
        # the call is NOT covered and pops.
        dest = pathlib.Path(self._tmp.name) / "goal_note3.txt"
        step = self._write_step(dest, content="hello plan")
        recorded = pa.approve_plan("goal-task", [step], [0])
        self.assertEqual(recorded["approved"], [0])
        result, calls = self._run_goal(
            dest, "different content",
            plan_approval=("goal-task", [step]))
        self.assertEqual(calls, ["write_file"])
        self.assertFalse(dest.exists())
        self.assertNotIn("Goal complete", result)

    def test_excluded_leg_always_asks_even_with_an_approval(self):
        # approve_plan refuses the send step outright, so no
        # record exists for it — the picked send still pops.
        dest = pathlib.Path(self._tmp.name) / "goal_note4.txt"
        steps = [
            self._write_step(dest),
            StepDef.from_mapping(
                {"tool": "send_email",
                 "args": {"to": "a@b.c", "subject": "s", "body": "b"}}),
        ]
        recorded = pa.approve_plan("goal-task", steps, [0, 1])
        self.assertEqual(recorded["approved"], [0])
        self.assertEqual(len(recorded["refused"]), 1)

        calls = []

        async def pick(goal, observation, history):
            if calls:
                return {"done": "done"}
            return {"tool": "send_email",
                    "args": {"to": "a@b.c", "subject": "s",
                             "body": "b"}}

        async def obs():
            return _ChangingSnapshot()()

        def verify(expect_text, before, after, result_text):
            return ("met", "goal state observed")

        self._deny_handler(calls)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = tl.TaskLedger(
                path=pathlib.Path(tmp) / "ledger.json")
            try:
                result = asyncio.run(rg.run_goal(
                    GOAL, pick_action=pick, observe=obs,
                    verify_expect=verify, ledger=ledger,
                    plan_approval=("goal-task", steps)))
            finally:
                confirm_gate.clear_handler()
        self.assertEqual(calls, ["send_email"])
        self.assertNotIn("Goal complete", result)

    def test_voice_goal_task_shape_covered(self):
        # The voice path's execution site IS run_goal: the
        # bridge owns a coordinator task exactly like this
        # (goal-level, empty steps, brain_id gemini_live) and
        # the text loop picks the actions. With a plan the
        # human approved for that task id, the picked action
        # runs without a fresh popup. (Residual: the bridge
        # does not pass a context yet — its task has no steps
        # and no plan dialog exists for voice goals — so this
        # proves the consult at the site the voice path runs
        # through, not a bridge flow that exists today.)
        dest = pathlib.Path(self._tmp.name) / "voice_note.txt"
        coord = tc.TurnCoordinator(
            ledger=tl.TaskLedger(
                path=pathlib.Path(self._tmp.name) / "ledger.json"))
        task = coord.new_task(GOAL, "multi_step", [],
                              turn_text=GOAL, brain_id="gemini_live")
        step = self._write_step(dest)
        recorded = pa.approve_plan(task.id, [step], [0])
        self.assertEqual(recorded["approved"], [0])
        result, calls = self._run_goal(
            dest, "hello plan", plan_approval=(task.id, [step]))
        self.assertEqual(calls, [])
        self.assertTrue(dest.is_file())
        self.assertIn("Goal complete", result)


# ---------------------------------------------------------------------------
# 3. Legacy run_routine loop through the REAL funnel
# ---------------------------------------------------------------------------

class LegacyLoopCoverageTests(_FunnelBase):
    def _routine(self, name, actions):
        return {"name": name, "actions": actions}

    def test_covered_legacy_step_runs_without_a_popup(self):
        dest = pathlib.Path(self._tmp.name) / "legacy_note.txt"
        step = self._write_step(dest)
        recorded = pa.approve_plan("legacy-task", [step], [0])
        self.assertEqual(recorded["approved"], [0])
        calls = []
        self._deny_handler(calls)
        routine = self._routine("leg", [
            {"tool": "write_file",
             "args": {"filepath": str(dest), "content": "hello plan"}}])
        try:
            with mock.patch.object(
                    pr, "_load_routines",
                    return_value=([routine], "")):
                result = pr.run_routine(
                    "leg", plan_approval=("legacy-task", [step]))
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(calls, [])
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.read_text(encoding="utf-8"), "hello plan")

    def test_uncovered_legacy_step_pops_and_denies(self):
        dest = pathlib.Path(self._tmp.name) / "legacy_note2.txt"
        calls = []
        self._deny_handler(calls)
        routine = self._routine("leg", [
            {"tool": "write_file",
             "args": {"filepath": str(dest), "content": "hello plan"}}])
        try:
            with mock.patch.object(
                    pr, "_load_routines",
                    return_value=([routine], "")):
                result = pr.run_routine("leg")
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(calls, ["write_file"])
        self.assertFalse(dest.exists())

    def test_digest_mismatch_pops_in_legacy_loop(self):
        dest = pathlib.Path(self._tmp.name) / "legacy_note3.txt"
        step = self._write_step(dest, content="hello plan")
        recorded = pa.approve_plan("legacy-task", [step], [0])
        self.assertEqual(recorded["approved"], [0])
        calls = []
        self._deny_handler(calls)
        routine = self._routine("leg", [
            {"tool": "write_file",
             "args": {"filepath": str(dest),
                      "content": "different content"}}])
        try:
            with mock.patch.object(
                    pr, "_load_routines",
                    return_value=([routine], "")):
                result = pr.run_routine(
                    "leg", plan_approval=("legacy-task", [step]))
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(calls, ["write_file"])
        self.assertFalse(dest.exists())

    def test_nesting_fallback_shows_the_plan_dialog_once(self):
        # The voice routine path's nesting fallback: the SAME
        # plan dialog the coordinator path shows, on the
        # routine's steps — then the legacy loop consults the
        # approval it recorded. A fake dialog approves the
        # safe step only; the nested step still halts the
        # loop (it can never run), and no popup fires.
        dest = pathlib.Path(self._tmp.name) / "nested_note.txt"
        dialog_calls = []
        confirm_calls = []
        self._deny_handler(confirm_calls)

        def fake_dialog(task, plan):
            dialog_calls.append(task)
            return [0]

        pa.register_plan_handler(fake_dialog)
        routine = self._routine("nested", [
            {"tool": "write_file",
             "args": {"filepath": str(dest), "content": "hello plan"}},
            {"tool": "run_routine", "args": {"name": "other"}},
        ])
        try:
            with mock.patch.object(
                    pr, "_load_routines",
                    return_value=([routine], "")):
                result = pr.run_routine_as_task("nested")
        finally:
            confirm_gate.clear_handler()
            pa.register_plan_handler(None)
        self.assertEqual(len(dialog_calls), 1)
        self.assertEqual(confirm_calls, [])
        self.assertTrue(dest.is_file())
        self.assertTrue(
            result.startswith(
                "Routine runs on the legacy loop: a nested"),
            result)
        self.assertIn("cannot run other routines", result)

    def test_nesting_fallback_without_a_dialog_asks_per_step(self):
        # No GUI handler registered (headless): no dialog, no
        # approval, the legacy loop runs with per-step asks,
        # exactly as before this batch.
        dest = pathlib.Path(self._tmp.name) / "nested_note2.txt"
        confirm_calls = []
        self._deny_handler(confirm_calls)
        routine = self._routine("nested", [
            {"tool": "write_file",
             "args": {"filepath": str(dest), "content": "hello plan"}},
            {"tool": "run_routine", "args": {"name": "other"}},
        ])
        try:
            with mock.patch.object(
                    pr, "_load_routines",
                    return_value=([routine], "")):
                result = pr.run_routine_as_task("nested")
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(confirm_calls, ["write_file"])
        self.assertFalse(dest.exists())
        self.assertTrue(
            result.startswith(
                "Routine runs on the legacy loop: a nested"),
            result)


# ---------------------------------------------------------------------------
# 4. Coordinator: parallel branches + re-plans consult by content
# ---------------------------------------------------------------------------

def _capturing_executor():
    """Fake executor recording (tool, args, plan channel live)."""
    def _run(tool, args):
        cur = pa.current()
        _run.seen.append((tool, dict(args),
                          dict(cur) if isinstance(cur, dict) else None))
        return {"status": "success", "kind": None, "result": "done"}
    _run.seen = []
    return _run


class _RecordingVerifier:
    """Per-expect scripted verdicts (B22 harness shape)."""

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}

    def __call__(self, expect_text, before, after, result_text):
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


class CoordinatorContentTests(unittest.TestCase):
    def setUp(self):
        pa.clear_all()
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        pa.clear_all()
        self._tmp.cleanup()

    def _coord(self, executor, verifier=None, brains=None):
        ledger = tl.TaskLedger(
            path=pathlib.Path(self._tmp.name) / "ledger.json")
        return tc.TurnCoordinator(
            executor=executor, verifier=verifier,
            snapshot=_Snapshot(), ledger=ledger,
            second_opinion_brains=brains), ledger

    def test_parallel_branch_covered_runs_sibling_asks(self):
        # Two branches, one parallel group: the approved
        # branch arms the channel (its own index, B19);
        # the sibling — same group, different content —
        # arms nothing, so the real funnel would pop for
        # it. Channel state is the proof at this layer;
        # Fix 8's funnel tests prove armed -> no popup and
        # unarmed -> popup.
        executor = _capturing_executor()
        coord, _ledger = self._coord(executor)
        dest_a = pathlib.Path(self._tmp.name) / "pa.txt"
        dest_b = pathlib.Path(self._tmp.name) / "pb.txt"
        task = coord.new_task(
            "write two notes", "saved_plan",
            [StepDef.from_mapping(
                {"tool": "write_file",
                 "args": {"filepath": str(dest_a), "content": "a"},
                 "parallel": "g"}),
             StepDef.from_mapping(
                {"tool": "write_file",
                 "args": {"filepath": str(dest_b), "content": "b"},
                 "parallel": "g"})])
        recorded = pa.approve_plan(task.id, task.steps, [0])
        self.assertEqual(recorded["approved"], [0])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(executor.seen), 2)
        # Parallel branches complete in either order — look
        # each branch up by its destination, not position.
        by_dest = {seen[1]["filepath"]: seen for seen in executor.seen}
        covered = by_dest[str(dest_a)]
        asking = by_dest[str(dest_b)]
        self.assertEqual(covered[0], "write_file")
        self.assertIsNotNone(covered[2])
        self.assertEqual(covered[2]["tool"], "write_file")
        self.assertEqual(covered[2]["index"], 0)
        self.assertEqual(asking[0], "write_file")
        self.assertIsNone(asking[2])

    def test_replanned_step_reusing_approved_content_runs_silently(self):
        # B22 re-plan, content-matched: the initial plan's
        # write-A is approved; the goal is not met, so the
        # brain re-plans with [write-A again (identical) +
        # write-C (new)]. The re-used step arms by content
        # (its NEW index has no record); the new step arms
        # nothing and would pop. The goal is met after the
        # re-plan, so the task completes.
        dest_a = pathlib.Path(self._tmp.name) / "ra.txt"
        dest_c = pathlib.Path(self._tmp.name) / "rc.txt"
        args_a = {"filepath": str(dest_a), "content": "a"}
        args_c = {"filepath": str(dest_c), "content": "c"}
        replan_json = json.dumps({"subgoals": [
            {"name": "retry", "steps": [
                {"tool": "write_file", "args": args_a,
                 "label": "write A again"},
                {"tool": "write_file", "args": args_c,
                 "label": "write C"},
            ]}]})
        verifier = _RecordingVerifier({
            GOAL: [("not-met", "not ready yet"),
                   ("met", "goal state observed")]})
        executor = _capturing_executor()
        coord, ledger = self._coord(
            executor, verifier=verifier,
            brains=(_ScriptedBrain([replan_json]),
                    _ScriptedBrain([""])))
        task = coord.new_task(
            GOAL, "saved_plan",
            [StepDef.from_mapping(
                {"tool": "write_file", "args": args_a})])
        recorded = pa.approve_plan(task.id, task.steps, [0])
        self.assertEqual(recorded["approved"], [0])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(receipt.as_dict()["replans"]), 1)
        self.assertEqual(len(executor.seen), 3)
        first, reused, new = executor.seen
        # Initial step: positional cover (index 0).
        self.assertEqual(first[1], args_a)
        self.assertIsNotNone(first[2])
        self.assertEqual(first[2]["index"], 0)
        # Re-planned identical step: content cover, armed at
        # the ORIGINAL approved index, not its new position.
        self.assertEqual(reused[1], args_a)
        self.assertIsNotNone(reused[2])
        self.assertEqual(reused[2]["index"], 0)
        # Re-plan's new step: no cover at all.
        self.assertEqual(new[1], args_c)
        self.assertIsNone(new[2])


if __name__ == "__main__":
    unittest.main()
