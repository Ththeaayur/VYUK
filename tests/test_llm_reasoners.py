# R7-3 — bounded beam search over candidate next actions for run_goal
# (maitrix-org/llm-reasoners pattern, VYUK's own code).
#
# The search asks the picker for up to beam_width candidates, scores
# each with the tree's own static signals, and feeds the BEST into
# the existing single pick. The picker contract does not change,
# the trust path does not change, the search never executes.
#
# Fakes only: no real cloud calls, no real tools, no real network.
# The picker is a script; the executor returns scripted funnel
# dicts; the observation changes every call.

import asyncio
import ast
import contextlib
import inspect
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from core.brain import goal_search as gs
from core.brain import planner_rungoal as rg
from core.brain import task_ledger as tl

GOAL = "organise the downloads folder"

SUCCESS = {"status": "success", "result": "did it"}


def _fake_verify(script):
    def _verify(expect_text, before, after, result_text):
        return script.get(expect_text, ("unverifiable", "no script"))
    return _verify


class _ChangingSnapshot:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return {"fg": "fake", "n": self.n, "windows": ["fake"],
                "texts": []}


def _injected_ctx():
    """Deterministic scoring context: two known tools with schemas,
    so the scores below are exact, not registry-dependent."""
    return gs.ScoringContext(
        known_tools={"fake_tool", "other_tool"},
        schemas={
            "fake_tool": {"type": "object",
                          "properties": {"x": {"type": "integer"}},
                          "required": ["x"]},
            "other_tool": {"type": "object", "properties": {}},
        })


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = gs.GoalSearchConfig()
        self.assertEqual(cfg.beam_width, 3)
        self.assertEqual(cfg.max_depth, 1)
        self.assertEqual(cfg.max_picker_calls, 12)
        self.assertEqual(cfg.reward_aggregator, "cumulative")

    def test_depth_hard_cap(self):
        with self.assertRaises(ValueError):
            gs.GoalSearchConfig(max_depth=3)
        with self.assertRaises(ValueError):
            gs.GoalSearchConfig(max_depth=0)

    def test_beam_width_bounds(self):
        with self.assertRaises(ValueError):
            gs.GoalSearchConfig(beam_width=0)
        with self.assertRaises(ValueError):
            gs.GoalSearchConfig(beam_width=9)

    def test_picker_calls_must_cover_beam(self):
        with self.assertRaises(ValueError):
            gs.GoalSearchConfig(beam_width=3, max_picker_calls=2)

    def test_normalise_search(self):
        self.assertIsNone(gs.normalise_search(None))
        self.assertEqual(gs.normalise_search(True),
                         gs.GoalSearchConfig())
        cfg = gs.GoalSearchConfig(beam_width=2)
        self.assertIs(gs.normalise_search(cfg), cfg)
        with self.assertRaises(TypeError):
            gs.normalise_search("yes")
        with self.assertRaises(TypeError):
            gs.normalise_search(3)


class ScorerTests(unittest.TestCase):
    def test_orders_candidates(self):
        ctx = _injected_ctx()
        best = gs.score_candidate(
            {"tool": "fake_tool", "args": {"x": 1},
             "expect": "thing visible"}, ctx, [])
        unknown = gs.score_candidate(
            {"tool": "ghost_tool", "args": {}}, ctx, [])
        invalid = gs.score_candidate(
            {"tool": "fake_tool", "args": {"x": "not an int"}}, ctx, [])
        self.assertEqual(best.score, 1.5)
        self.assertEqual(best.breakdown,
                         {"known_tool": 0.5, "args_valid": 0.5,
                          "declares_expect": 0.5, "repeat_penalty": 0.0})
        self.assertEqual(unknown.score, -1.0)
        self.assertEqual(invalid.score, -0.5)
        self.assertGreater(best.score, unknown.score)
        self.assertGreater(best.score, invalid.score)

    def test_repeat_of_retry_is_penalised(self):
        ctx = _injected_ctx()
        fresh = gs.score_candidate(
            {"tool": "fake_tool", "args": {"x": 1}}, ctx, [])
        repeated = gs.score_candidate(
            {"tool": "fake_tool", "args": {"x": 1}}, ctx,
            [{"event": "retry", "tool": "fake_tool",
              "reason": "no change"}])
        self.assertEqual(fresh.score, 1.0)
        self.assertEqual(repeated.score, 0.0)
        self.assertEqual(repeated.breakdown["repeat_penalty"], -1.0)

    def test_earlier_retry_penalty_is_half(self):
        ctx = _injected_ctx()
        scored = gs.score_candidate(
            {"tool": "fake_tool", "args": {"x": 1}}, ctx,
            [{"event": "completed", "step": 1, "tool": "other_tool"},
             {"event": "retry", "tool": "other_tool",
              "reason": "no change"},
             {"event": "retry", "tool": "fake_tool",
              "reason": "no change"}])
        # Most recent retry is fake_tool (-1.0); other_tool is
        # earlier — but the candidate IS fake_tool, so -1.0.
        self.assertEqual(scored.breakdown["repeat_penalty"], -1.0)
        other = gs.score_candidate(
            {"tool": "other_tool", "args": {}}, ctx,
            [{"event": "retry", "tool": "other_tool",
              "reason": "no change"},
             {"event": "completed", "step": 2, "tool": "fake_tool"}])
        self.assertEqual(other.breakdown["repeat_penalty"], -1.0)

    def test_malformed_returns_none(self):
        ctx = _injected_ctx()
        self.assertIsNone(gs.score_candidate({"args": {}}, ctx, []))
        self.assertIsNone(gs.score_candidate("not a dict", ctx, []))
        self.assertIsNone(gs.score_candidate({"tool": ""}, ctx, []))

    def test_no_registry_signal_is_zero_not_a_crash(self):
        ctx = gs.ScoringContext(known_tools=None, schemas={})
        scored = gs.score_candidate(
            {"tool": "fake_tool", "args": {"x": 1},
             "expect": "thing visible"}, ctx, [])
        self.assertEqual(scored.score, 0.5)  # expect only
        self.assertEqual(scored.breakdown["known_tool"], 0.0)
        self.assertEqual(scored.breakdown["args_valid"], 0.0)


class WorldTests(unittest.TestCase):
    def test_step_never_simulates(self):
        world = gs.GoalWorld(max_depth=1)
        obs = {"fg": "fake"}
        state = world.init_state(obs, [{"event": "completed"}])
        nxt, aux = world.step(state, {"tool": "fake_tool", "args": {}})
        self.assertIs(nxt.observation, obs)  # same object, unchanged
        self.assertEqual(nxt.depth, 1)
        self.assertEqual(len(nxt.history), 2)
        self.assertEqual(nxt.history[1]["event"], "hypothetical")
        self.assertEqual(len(state.history), 1)  # input untouched
        self.assertTrue(world.is_terminal(nxt))
        self.assertFalse(world.is_terminal(state))


class SearchPickTests(unittest.TestCase):
    def _run_pick(self, actions, config=None, history=None):
        """Drive search_pick with a cycling fake picker. actions is
        the cycle; the picker returns the next action each call."""
        calls = {"n": 0}

        async def pick(goal, observation, history):
            action = actions[calls["n"] % len(actions)]
            calls["n"] += 1
            return dict(action)

        cfg = config or gs.GoalSearchConfig()
        with mock.patch.object(gs, "build_scoring_context",
                               return_value=_injected_ctx()):
            action, report = asyncio.run(gs.search_pick(
                cfg, pick, GOAL, {"fg": "fake"}, history or []))
        return action, report, calls["n"]

    def test_best_of_three_is_picked(self):
        action, report, _n = self._run_pick([
            {"tool": "ghost_tool", "args": {}},
            {"tool": "fake_tool", "args": {"x": 1}},
            {"tool": "fake_tool", "args": {"x": 1},
             "expect": "thing visible"},
        ])
        self.assertEqual(action["tool"], "fake_tool")
        self.assertEqual(action.get("expect"), "thing visible")
        self.assertEqual(report["distinct"], 2)
        self.assertEqual(report["best_score"], 1.5)

    def test_deterministic_picker_degenerates_to_greedy(self):
        action, report, n = self._run_pick([
            {"tool": "fake_tool", "args": {"x": 1}},
        ])
        self.assertEqual(action, {"tool": "fake_tool", "args": {"x": 1}})
        self.assertEqual(report["distinct"], 1)
        self.assertLessEqual(n, 5)  # first + capped re-samples

    def test_first_done_returns_immediately(self):
        async def pick(goal, observation, history):
            return {"done": "all done"}

        with mock.patch.object(gs, "build_scoring_context",
                               return_value=_injected_ctx()):
            action, report = asyncio.run(gs.search_pick(
                gs.GoalSearchConfig(), pick, GOAL, {"fg": "fake"}, []))
        self.assertEqual(action, {"done": "all done"})

    def test_later_done_is_dropped(self):
        calls = {"n": 0}

        async def pick(goal, observation, history):
            calls["n"] += 1
            if calls["n"] == 2:
                return {"done": "all done"}
            return {"tool": "fake_tool", "args": {"x": 1}}

        with mock.patch.object(gs, "build_scoring_context",
                               return_value=_injected_ctx()):
            action, _report = asyncio.run(gs.search_pick(
                gs.GoalSearchConfig(), pick, GOAL, {"fg": "fake"}, []))
        self.assertEqual(action["tool"], "fake_tool")
        self.assertNotIn("done", action)

    def test_all_none_returns_none(self):
        async def pick(goal, observation, history):
            return None

        with mock.patch.object(gs, "build_scoring_context",
                               return_value=_injected_ctx()):
            action, _report = asyncio.run(gs.search_pick(
                gs.GoalSearchConfig(), pick, GOAL, {"fg": "fake"}, []))
        self.assertIsNone(action)

    def test_picker_failure_propagates(self):
        async def pick(goal, observation, history):
            raise RuntimeError("picker exploded")

        with mock.patch.object(gs, "build_scoring_context",
                               return_value=_injected_ctx()):
            with self.assertRaises(RuntimeError):
                asyncio.run(gs.search_pick(
                    gs.GoalSearchConfig(), pick, GOAL, {"fg": "fake"},
                    []))

    def test_picker_calls_capped(self):
        # A picker that returns a NEW distinct action every call:
        # the step budget must cap the total calls.
        calls = {"n": 0}

        async def pick(goal, observation, history):
            calls["n"] += 1
            return {"tool": f"tool_{calls['n']}", "args": {}}

        cfg = gs.GoalSearchConfig(beam_width=4, max_picker_calls=4)
        with mock.patch.object(gs, "build_scoring_context",
                               return_value=gs.ScoringContext(
                                   known_tools=set(), schemas={})):
            _action, _report = asyncio.run(gs.search_pick(
                cfg, pick, GOAL, {"fg": "fake"}, []))
        self.assertEqual(calls["n"], 4)

    def test_depth_two_returns_first_action_and_no_leak(self):
        history = [{"event": "completed", "step": 1,
                    "tool": "fake_tool", "result": "ok"}]
        action, report, _n = self._run_pick([
            {"tool": "fake_tool", "args": {"x": 1}},
            {"tool": "other_tool", "args": {}},
        ], config=gs.GoalSearchConfig(beam_width=2, max_depth=2),
            history=history)
        self.assertEqual(report["depth_reached"], 2)
        self.assertEqual(action["tool"], "fake_tool")
        self.assertEqual(report["best_score"], 2.0)
        self.assertEqual(len(history), 1)  # real history untouched
        self.assertNotEqual(history[0].get("event"), "hypothetical")

    def test_no_torch_or_reasoners_import(self):
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("reasoners", sys.modules)
        src = pathlib.Path(gs.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0],
                                     ("torch", "transformers",
                                      "reasoners"))
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0],
                                 ("torch", "transformers", "reasoners"))


class RungoalSearchTests(unittest.TestCase):
    def _ledger(self, tmp):
        return tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")

    def _run(self, pick, ledger, executor, verify_script=None,
             budgets=None, search=None):
        """Run run_goal with a scripted executor + changing
        snapshot, exactly like test_rungoal_retry's harness."""
        snap = _ChangingSnapshot()

        async def _pick(goal, observation, history):
            out = pick(goal, observation, history)
            if asyncio.iscoroutine(out):
                out = await out
            return out

        async def _obs():
            return snap()

        def _budget_int(name, default):
            return (budgets or {}).get(name, default)

        def _budget_seconds(name, default):
            return (budgets or {}).get(name, default)

        patches = [
            mock.patch.object(rg, "_goal_budget_int",
                              side_effect=_budget_int),
            mock.patch.object(rg, "_goal_budget_seconds",
                              side_effect=_budget_seconds),
            mock.patch.object(rg, "_known_tools",
                              return_value={"fake_tool", "other_tool"}),
            mock.patch.object(rg, "_execute_tool",
                              side_effect=executor),
        ]
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            return asyncio.run(rg.run_goal(
                GOAL, pick_action=_pick, observe=_obs,
                verify_expect=_fake_verify(verify_script or {}),
                ledger=ledger, search=search))

    def _cycling_picker(self, executed):
        """Cycle [other_tool, ghost, fake_tool+expect]; once a step
        has executed, say done. The greedy first pick is
        other_tool; the search best is fake_tool+expect."""
        cycle = [
            {"tool": "other_tool", "args": {}},
            {"tool": "ghost_tool", "args": {}},
            {"tool": "fake_tool", "args": {"x": 1},
             "expect": "thing visible"},
        ]
        state = {"n": 0}

        async def pick(goal, observation, history):
            if executed:
                return {"done": "all done"}
            action = cycle[state["n"] % len(cycle)]
            state["n"] += 1
            return dict(action)

        return pick

    def test_search_picks_best_and_executes_through_normal_path(self):
        executed = []

        def executor(tool, args):
            executed.append((tool, args))
            return dict(SUCCESS)

        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(
                self._cycling_picker(executed), self._ledger(tmp),
                executor,
                verify_script={GOAL: ("met", "goal state observed"),
                               "thing visible": ("met", "thing visible")},
                search=gs.GoalSearchConfig())
        self.assertEqual(executed, [("fake_tool", {"x": 1})])
        self.assertIn("🔍 search:", result)
        self.assertIn("Goal step 1 [fake_tool]", result)
        self.assertIn("[expect met]", result)
        self.assertIn("Goal complete: all done", result)

    def test_search_off_is_greedy(self):
        executed = []

        def executor(tool, args):
            executed.append((tool, args))
            return dict(SUCCESS)

        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(
                self._cycling_picker(executed), self._ledger(tmp),
                executor,
                verify_script={GOAL: ("met", "goal state observed")},
                search=None)
        self.assertEqual(executed, [("other_tool", {})])
        self.assertNotIn("🔍 search:", result)
        self.assertIn("Goal complete: all done", result)

    def test_beam_width_one_degenerates_to_greedy(self):
        executed = []

        def executor(tool, args):
            executed.append((tool, args))
            return dict(SUCCESS)

        with tempfile.TemporaryDirectory() as tmp:
            self._run(
                self._cycling_picker(executed), self._ledger(tmp),
                executor,
                verify_script={GOAL: ("met", "goal state observed")},
                search=gs.GoalSearchConfig(beam_width=1))
        self.assertEqual(executed, [("other_tool", {})])

    def test_search_unknown_best_still_hits_registry_gate(self):
        executed = []

        def executor(tool, args):
            executed.append((tool, args))
            return dict(SUCCESS)

        async def pick(goal, observation, history):
            if executed:
                return {"done": "all done"}
            return {"tool": "ghost_tool", "args": {}}

        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(
                pick, self._ledger(tmp), executor,
                verify_script={GOAL: ("met", "goal state observed")},
                search=gs.GoalSearchConfig())
        self.assertEqual(executed, [])
        self.assertIn("is not a registered tool", result)

    def test_search_picker_failure_halts_like_greedy(self):
        async def pick(goal, observation, history):
            raise RuntimeError("picker exploded")

        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(
                pick, self._ledger(tmp),
                lambda tool, args: dict(SUCCESS),
                verify_script={GOAL: ("met", "goal state observed")},
                search=gs.GoalSearchConfig())
        self.assertIn("picker failed", result)

    def test_search_bad_flag_fails_fast(self):
        async def pick(goal, observation, history):
            return {"done": "all done"}

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TypeError):
                self._run(
                    pick, self._ledger(tmp),
                    lambda tool, args: dict(SUCCESS),
                    verify_script={GOAL: ("met", "goal state observed")},
                    search="yes")


class BrainPassthroughTests(unittest.TestCase):
    def test_brains_accept_and_forward_search(self):
        from core.voice.cloud_text_goal import GroqSessionGoalMixin
        from core.voice.local_client_plangoal import (
            LocalLiveSessionPlanGoalMixin)
        for mixin in (GroqSessionGoalMixin,
                      LocalLiveSessionPlanGoalMixin):
            params = inspect.signature(mixin.run_goal).parameters
            self.assertIn("search", params)
            self.assertIsNone(params["search"].default)
        for path in ("core/voice/cloud_text_goal.py",
                     "core/voice/local_client_plangoal.py"):
            src = pathlib.Path(path).read_text(encoding="utf-8")
            self.assertIn('"search": search', src)
            self.assertIn("search=search", src)


if __name__ == "__main__":
    unittest.main()
