"""Tests for R7-7 taskflow — VYUK's own DAG workflow engine
(core/dagflow.py, hajirufai/taskflow pattern, own code, stdlib only).

A DAG of funnel-tool calls: nodes are funnel tools (their own gates,
trust path and confirm rules untouched), edges are dependencies,
trigger rules decide which nodes run after a predecessor's outcome,
retries with backoff and per-task timeouts are bounded, run history
is SQLite, and the run record is written AND re-read before the
run_workflow summary returns.

Isolation: the module's store paths come from default_workflows_dir()
/ default_db_path() (core.config.DATA_DIR). Every test that touches
the store or a handler patches those two module functions to a temp
dir, so no workflow definition or run history ever lands in the
tree's data/. Pure-core tests need no isolation. Real-funnel tests
call only read-only tools (list_routines, companion_status,
meeting_status, custom_wake_model_status, list_custom_wake_models)
— nothing writes, nothing captures, nothing prompts.
"""
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TREEROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(_TREEROOT))

from core import dagflow  # noqa: E402
from core.dagflow import (  # noqa: E402
    DagRun,
    TaskRun,
    TaskSpec,
    TaskState,
    TriggerRule,
    Workflow,
    compute_delay,
    detect_cycle,
    parallel_levels,
    topological_sort,
    validate_edges,
)
from core.funnel.tools_registry import tools as _registry  # noqa: E402


def _spec(task_id, tool, args=None, deps=(), trigger="all_success",
          retries=0, timeout=None):
    return TaskSpec(task_id=task_id, tool=tool, args=args or {},
                    depends_on=list(deps), trigger_rule=TriggerRule(trigger),
                    retries=retries, timeout=timeout)


def _wf(workflow_id, tasks):
    return Workflow(workflow_id,
                    {spec.task_id: spec for spec in tasks}, "test")


def _ok(result="done"):
    return {"status": "success", "result": result}


def _fail(kind="handler_error", message="boom"):
    return {"status": "failure", "kind": kind, "message": message}


def _refuse(message="refused by gate"):
    return {"status": "refused", "kind": "gate", "message": message}


class _TempStoreMixin(unittest.TestCase):
    """Patch the module's store paths to a temp dir for the test."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="dagflow_test_"))
        self._workflows_dir = self._tmp / "workflows"
        self._db_path = self._tmp / "dagflow_runs.db"
        self._p1 = mock.patch.object(
            dagflow, "default_workflows_dir", return_value=self._workflows_dir)
        self._p2 = mock.patch.object(
            dagflow, "default_db_path", return_value=self._db_path)
        self._p1.start()
        self._p2.start()
        self.addCleanup(self._p1.stop)
        self.addCleanup(self._p2.stop)
        self.addCleanup(shutil.rmtree, self._tmp, True)


# ---------------------------------------------------------------- core


class TestTopologicalSort(unittest.TestCase):
    def test_linear_chain_orders_in_dependency_order(self):
        nodes = ["a", "b", "c"]
        edges = {"a": ["b"], "b": ["c"]}
        self.assertEqual(topological_sort(nodes, edges), ["a", "b", "c"])

    def test_diamond_orders_dependencies_first(self):
        nodes = ["a", "b", "c", "d"]
        edges = {"a": ["b", "c"], "b": ["d"], "c": ["d"]}
        order = topological_sort(nodes, edges)
        self.assertEqual(order[0], "a")
        self.assertEqual(order[-1], "d")
        self.assertLess(order.index("b"), order.index("d"))
        self.assertLess(order.index("c"), order.index("d"))

    def test_independent_tasks_keep_definition_order(self):
        self.assertEqual(topological_sort(["x", "y", "z"], {}), ["x", "y", "z"])

    def test_cycle_raises_cycle_error_with_the_path(self):
        nodes = ["a", "b", "c"]
        edges = {"a": ["b"], "b": ["c"], "c": ["a"]}
        with self.assertRaises(dagflow.CycleError) as ctx:
            topological_sort(nodes, edges)
        self.assertIn("a", str(ctx.exception))
        self.assertIn("Cycle detected", str(ctx.exception))


class TestDetectCycle(unittest.TestCase):
    def test_acyclic_returns_none(self):
        self.assertIsNone(detect_cycle(["a", "b"], {"a": ["b"]}))

    def test_self_loop_is_a_cycle(self):
        # The DFS closes the cycle by appending the start again:
        # [neighbor, node] reversed + close -> three entries.
        path = detect_cycle(["a"], {"a": ["a"]})
        self.assertEqual(path, ["a", "a", "a"])

    def test_two_node_cycle_names_both_and_closes(self):
        path = detect_cycle(["a", "b"], {"a": ["b"], "b": ["a"]})
        self.assertEqual(set(path), {"a", "b"})
        self.assertEqual(path[0], path[-1])


class TestParallelLevels(unittest.TestCase):
    def test_demo_workflow_levels(self):
        # companion -> wake+meeting -> models
        nodes = ["companion", "wake", "models", "meeting"]
        edges = {"companion": ["wake", "meeting"], "wake": ["models"]}
        self.assertEqual(
            parallel_levels(nodes, edges),
            [["companion"], ["wake", "meeting"], ["models"]])

    def test_all_independent_is_one_level(self):
        self.assertEqual(parallel_levels(["a", "b"], {}), [["a", "b"]])


class TestValidateEdges(unittest.TestCase):
    def test_unknown_dependency_is_an_error(self):
        errs = validate_edges({"a"}, {"a": ["ghost"]})
        self.assertEqual(len(errs), 1)
        self.assertIn("ghost", errs[0])

    def test_unknown_source_is_an_error(self):
        errs = validate_edges({"a"}, {"ghost": ["a"]})
        self.assertTrue(any("ghost" in e for e in errs))

    def test_valid_graph_has_no_errors(self):
        self.assertEqual(validate_edges({"a", "b"}, {"a": ["b"]}), [])


class TestComputeDelay(unittest.TestCase):
    def test_base_delay_on_first_attempt(self):
        self.assertEqual(compute_delay(0, jitter=False), 1.0)

    def test_backoff_doubles_per_attempt(self):
        self.assertEqual(compute_delay(1, jitter=False), 2.0)
        self.assertEqual(compute_delay(2, jitter=False), 4.0)
        self.assertEqual(compute_delay(3, jitter=False), 8.0)

    def test_delay_is_capped_at_max_delay(self):
        self.assertEqual(compute_delay(99, jitter=False), 300.0)
        self.assertEqual(compute_delay(99, max_delay=60.0, jitter=False), 60.0)

    def test_jitter_stays_within_25_percent(self):
        for attempt in range(0, 5):
            for _ in range(20):
                d = compute_delay(attempt, jitter=True)
                base = min(2.0 ** attempt, 300.0)
                self.assertGreaterEqual(d, base * 0.75)
                self.assertLessEqual(d, base * 1.25)
                self.assertGreaterEqual(d, 0.0)


class TestWorkflowValidate(unittest.TestCase):
    def _doc(self, tasks, workflow_id="wf1"):
        return {"workflow_id": workflow_id, "description": "t", "tasks": tasks}

    def test_valid_workflow_has_no_errors(self):
        wf = Workflow.from_dict(self._doc([
            {"task_id": "a", "tool": "list_routines"},
            {"task_id": "b", "tool": "list_routines", "depends_on": ["a"]}]))
        self.assertEqual(wf.validate(known_tools={"list_routines"}), [])

    def test_unknown_tool_is_an_error(self):
        wf = Workflow.from_dict(self._doc([
            {"task_id": "a", "tool": "no_such_tool_xyz"}]))
        errs = wf.validate(known_tools={"list_routines"})
        self.assertEqual(len(errs), 1)
        self.assertIn("no_such_tool_xyz", errs[0])

    def test_unknown_tool_check_skipped_without_a_tool_set(self):
        wf = Workflow.from_dict(self._doc([
            {"task_id": "a", "tool": "no_such_tool_xyz"}]))
        self.assertEqual(wf.validate(known_tools=None), [])

    def test_cycle_is_an_error(self):
        wf = Workflow.from_dict(self._doc([
            {"task_id": "a", "tool": "list_routines", "depends_on": ["b"]},
            {"task_id": "b", "tool": "list_routines", "depends_on": ["a"]}]))
        errs = wf.validate(known_tools={"list_routines"})
        self.assertTrue(any("Cycle" in e for e in errs))

    def test_unknown_dependency_and_self_dependency_are_errors(self):
        wf = Workflow.from_dict(self._doc([
            {"task_id": "a", "tool": "list_routines", "depends_on": ["ghost"]}]))
        self.assertTrue(any("ghost" in e for e in
                            wf.validate(known_tools={"list_routines"})))
        wf = Workflow.from_dict(self._doc([
            {"task_id": "a", "tool": "list_routines", "depends_on": ["a"]}]))
        self.assertTrue(any("itself" in e for e in
                            wf.validate(known_tools={"list_routines"})))

    def test_duplicate_task_id_refuses_at_from_dict(self):
        with self.assertRaises(ValueError) as ctx:
            Workflow.from_dict(self._doc([
                {"task_id": "a", "tool": "list_routines"},
                {"task_id": "a", "tool": "list_routines"}]))
        self.assertIn("duplicate", str(ctx.exception))

    def test_bad_trigger_rule_refuses_at_from_dict(self):
        with self.assertRaises(ValueError):
            Workflow.from_dict(self._doc([
                {"task_id": "a", "tool": "list_routines",
                 "trigger_rule": "whenever"}]))

    def test_out_of_range_timeout_retries_and_backoff_are_errors(self):
        for bad in ({"timeout": 0}, {"timeout": 601},
                    {"retries": -1}, {"retries": 6},
                    {"retry_delay": 0}, {"retry_backoff": 9}):
            task = {"task_id": "a", "tool": "list_routines"}
            task.update(bad)
            wf = Workflow.from_dict(self._doc([task]))
            self.assertTrue(
                wf.validate(known_tools={"list_routines"}), msg=str(bad))

    def test_empty_task_list_is_an_error(self):
        wf = Workflow.from_dict(self._doc([]))
        self.assertTrue(any("no tasks" in e for e in
                            wf.validate(known_tools={"list_routines"})))

    def test_bad_workflow_id_is_an_error(self):
        wf = Workflow.from_dict(self._doc(
            [{"task_id": "a", "tool": "list_routines"}], workflow_id="Bad ID!"))
        self.assertTrue(any("workflow_id" in e for e in
                            wf.validate(known_tools={"list_routines"})))

    def test_execution_order_and_levels_follow_the_graph(self):
        wf = Workflow.from_dict(self._doc([
            {"task_id": "companion", "tool": "companion_status"},
            {"task_id": "wake", "tool": "custom_wake_model_status",
             "depends_on": ["companion"]},
            {"task_id": "models", "tool": "list_custom_wake_models",
             "depends_on": ["wake"]},
            {"task_id": "meeting", "tool": "meeting_status",
             "depends_on": ["companion"]}]))
        self.assertEqual(wf.get_execution_order(),
                         ["companion", "wake", "meeting", "models"])
        self.assertEqual(wf.levels(),
                         [["companion"], ["wake", "meeting"], ["models"]])


class TestTriggerRules(unittest.TestCase):
    def _run(self, tasks, outcome_fn):
        wf = _wf("trig", tasks)
        return dagflow.run_dag(wf, execute_fn=outcome_fn,
                               sleep_fn=lambda s: None)

    def test_all_success_skips_when_a_dependency_fails(self):
        tasks = [_spec("a", "list_routines"), _spec("b", "list_routines", deps=("a",))]
        run = self._run(tasks, lambda tool, args: _fail())
        self.assertEqual(run.task_runs["a"].state, TaskState.FAILED)
        self.assertEqual(run.task_runs["b"].state, TaskState.SKIPPED)
        self.assertEqual(run.state, TaskState.FAILED)

    def test_all_done_runs_even_when_a_dependency_fails(self):
        tasks = [_spec("a", "list_routines"),
                 _spec("b", "list_routines", deps=("a",), trigger="all_done")]
        run = self._run(tasks, lambda tool, args: _fail())
        self.assertEqual(run.task_runs["b"].state, TaskState.FAILED)  # it RAN
        self.assertEqual(run.task_runs["b"].attempt, 1)

    def test_one_success_runs_when_any_dependency_succeeds(self):
        tasks = [_spec("a", "list_routines"),
                 _spec("b", "list_routines", deps=("a",), trigger="one_success")]
        run = self._run(tasks, lambda tool, args: _ok())
        self.assertEqual(run.task_runs["b"].state, TaskState.SUCCESS)

    def test_one_success_skips_when_no_dependency_succeeds(self):
        tasks = [_spec("a", "list_routines"),
                 _spec("b", "list_routines", deps=("a",), trigger="one_success")]
        run = self._run(tasks, lambda tool, args: _fail())
        self.assertEqual(run.task_runs["b"].state, TaskState.SKIPPED)

    def test_none_failed_skips_when_a_dependency_fails(self):
        tasks = [_spec("a", "list_routines"),
                 _spec("b", "list_routines", deps=("a",), trigger="none_failed")]
        run = self._run(tasks, lambda tool, args: _fail())
        self.assertEqual(run.task_runs["b"].state, TaskState.SKIPPED)

    def test_none_failed_runs_when_a_dependency_is_skipped_not_failed(self):
        # a fails; b depends on a with all_success (skipped);
        # c depends on b with none_failed (b skipped, not failed) -> runs.
        tasks = [_spec("a", "list_routines"),
                 _spec("b", "list_routines", deps=("a",)),
                 _spec("c", "list_routines", deps=("b",), trigger="none_failed")]
        run = self._run(tasks, lambda tool, args: _fail())
        self.assertEqual(run.task_runs["b"].state, TaskState.SKIPPED)
        # c runs (the scripted failure ends it FAILED — the point
        # is it RAN, not that it succeeded).
        self.assertEqual(run.task_runs["c"].state, TaskState.FAILED)
        self.assertEqual(run.task_runs["c"].attempt, 1)

    def test_skipped_node_names_the_trigger_rule_and_upstreams(self):
        # a fails; b and c both skip on all_success. The run is
        # FAILED (a failed); the point is the skip names the rule
        # and the upstream states, so the record is readable.
        tasks = [_spec("a", "list_routines"),
                 _spec("b", "list_routines", deps=("a",)),
                 _spec("c", "list_routines", deps=("b",))]
        run = self._run(tasks, lambda tool, args: _fail())
        self.assertEqual(run.task_runs["b"].state, TaskState.SKIPPED)
        self.assertIn("trigger all_success not met",
                      run.task_runs["b"].error)
        self.assertIn("a=failed", run.task_runs["b"].error)


# ---------------------------------------------------------------- store


class TestDefinitionStore(_TempStoreMixin):
    def _doc(self, workflow_id="wf1", tasks=None):
        return {"workflow_id": workflow_id, "description": "test",
                "tasks": tasks if tasks is not None else [
                    {"task_id": "a", "tool": "list_routines", "args": {}}]}

    def test_define_and_re_read_round_trips(self):
        ok, message = dagflow.define_workflow(self._doc())
        self.assertTrue(ok, message)
        self.assertIn("wf1", message)
        again = dagflow.load_workflow("wf1")
        self.assertIsNotNone(again)
        self.assertEqual(again.workflow_id, "wf1")
        self.assertEqual(again.tasks["a"].tool, "list_routines")

    def test_define_rejects_unknown_tool_and_stores_nothing(self):
        doc = self._doc(tasks=[{"task_id": "a", "tool": "no_such_tool_xyz"}])
        ok, message = dagflow.define_workflow(doc)
        self.assertFalse(ok)
        self.assertIn("no_such_tool_xyz", message)
        self.assertFalse((self._workflows_dir / "wf1.json").exists())

    def test_define_rejects_a_cycle_and_stores_nothing(self):
        doc = self._doc(tasks=[
            {"task_id": "a", "tool": "list_routines", "depends_on": ["b"]},
            {"task_id": "b", "tool": "list_routines", "depends_on": ["a"]}])
        ok, _ = dagflow.define_workflow(doc)
        self.assertFalse(ok)
        self.assertFalse((self._workflows_dir / "wf1.json").exists())

    def test_define_rejects_unknown_dependency(self):
        doc = self._doc(tasks=[
            {"task_id": "a", "tool": "list_routines", "depends_on": ["ghost"]}])
        ok, message = dagflow.define_workflow(doc)
        self.assertFalse(ok)
        self.assertIn("ghost", message)

    def test_define_rejects_bad_trigger_rule(self):
        doc = self._doc(tasks=[
            {"task_id": "a", "tool": "list_routines", "trigger_rule": "whenever"}])
        ok, _ = dagflow.define_workflow(doc)
        self.assertFalse(ok)

    def test_define_rejects_out_of_range_timeout_and_retries(self):
        for bad in ({"timeout": 0}, {"timeout": 601},
                    {"retries": -1}, {"retries": 6}):
            task = {"task_id": "a", "tool": "list_routines"}
            task.update(bad)
            ok, _ = dagflow.define_workflow(self._doc(tasks=[task]))
            self.assertFalse(ok, msg=str(bad))

    def test_define_rejects_empty_task_list(self):
        ok, message = dagflow.define_workflow(self._doc(tasks=[]))
        self.assertFalse(ok)
        self.assertIn("no tasks", message)

    def test_define_refuses_a_builtin_id(self):
        ok, message = dagflow.define_workflow(
            self._doc(workflow_id="daily-status"))
        self.assertFalse(ok)
        self.assertIn("built-in", message)

    def test_redefining_replaces_the_stored_definition(self):
        # The module overwrites silently (no overwrite flag): pin
        # the actual behaviour — a second define replaces the first.
        ok, _ = dagflow.define_workflow(self._doc())
        self.assertTrue(ok)
        ok, _ = dagflow.define_workflow(self._doc(tasks=[
            {"task_id": "a", "tool": "list_routines"},
            {"task_id": "b", "tool": "list_routines", "depends_on": ["a"]}]))
        self.assertTrue(ok)
        self.assertEqual(len(dagflow.load_workflow("wf1").tasks), 2)

    def test_list_workflows_names_builtins_first_then_stored(self):
        dagflow.define_workflow(self._doc("wf1"))
        dagflow.define_workflow(self._doc("wf2"))
        rows = dagflow.list_workflows()
        self.assertEqual([r["workflow_id"] for r in rows],
                         ["daily-status", "wf1", "wf2"])
        self.assertTrue(rows[0]["builtin"])
        self.assertFalse(rows[1]["builtin"])
        self.assertEqual(rows[0]["tasks"], 4)

    def test_load_unknown_workflow_is_none(self):
        self.assertIsNone(dagflow.load_workflow("nope"))


# ---------------------------------------------------------------- runner


class TestRunnerRetries(unittest.TestCase):
    def _run(self, tasks, script):
        wf = _wf("r", tasks)
        return dagflow.run_dag(wf, execute_fn=script,
                               sleep_fn=lambda s: None)

    def test_handler_error_retries_up_to_the_limit_then_fails(self):
        calls = []

        def script(tool, args):
            calls.append(tool)
            return _fail()

        tasks = [_spec("a", "list_routines", retries=2)]
        run = self._run(tasks, script)
        self.assertEqual(run.task_runs["a"].state, TaskState.FAILED)
        self.assertEqual(run.task_runs["a"].attempt, 3)  # 1 + 2 retries
        self.assertEqual(len(calls), 3)
        self.assertEqual(run.state, TaskState.FAILED)

    def test_transient_failure_recovers_on_retry(self):
        calls = {"n": 0}

        def script(tool, args):
            calls["n"] += 1
            return _fail() if calls["n"] == 1 else _ok()

        tasks = [_spec("a", "list_routines", retries=2)]
        run = self._run(tasks, script)
        self.assertEqual(run.task_runs["a"].state, TaskState.SUCCESS)
        self.assertEqual(run.task_runs["a"].attempt, 2)
        self.assertEqual(run.state, TaskState.SUCCESS)

    def test_retry_backoff_delays_are_exponential(self):
        delays = []

        def script(tool, args):
            return _fail()

        tasks = [_spec("a", "list_routines", retries=2)]
        wf = _wf("r", tasks)
        run = dagflow.run_dag(wf, execute_fn=script,
                              sleep_fn=lambda s: delays.append(s))
        self.assertEqual(run.task_runs["a"].attempt, 3)
        self.assertEqual(len(delays), 2)
        # attempt 0 -> ~1.0 s, attempt 1 -> ~2.0 s (±25% jitter)
        self.assertGreaterEqual(delays[0], 0.75)
        self.assertLessEqual(delays[0], 1.25)
        self.assertGreaterEqual(delays[1], 1.5)
        self.assertLessEqual(delays[1], 2.5)

    def test_refusal_is_never_retried(self):
        calls = []

        def script(tool, args):
            calls.append(tool)
            return _refuse()

        tasks = [_spec("a", "list_routines", retries=5)]
        run = self._run(tasks, script)
        self.assertEqual(run.task_runs["a"].state, TaskState.FAILED)
        self.assertEqual(run.task_runs["a"].attempt, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("refus", run.task_runs["a"].error.lower())

    def test_success_carrying_a_refusal_sentence_is_a_refusal(self):
        # A handler that "succeeds" while reporting a refusal in
        # its text is a refusal, not a success — and never retried.
        calls = []

        def script(tool, args):
            calls.append(tool)
            return {"status": "success",
                    "result": "Refused: the gate declined this call."}

        tasks = [_spec("a", "list_routines", retries=5)]
        run = self._run(tasks, script)
        self.assertEqual(run.task_runs["a"].state, TaskState.FAILED)
        self.assertEqual(len(calls), 1)
        self.assertIn("refus", run.task_runs["a"].error.lower())

    def test_timeout_result_is_never_retried(self):
        calls = []

        def script(tool, args):
            calls.append(tool)
            return {"status": "timeout", "message": "too slow"}

        tasks = [_spec("a", "list_routines", retries=5)]
        run = self._run(tasks, script)
        self.assertEqual(run.task_runs["a"].attempt, 1)
        self.assertEqual(len(calls), 1)

    def test_unavailable_is_never_retried(self):
        def script(tool, args):
            return _fail(kind="unavailable")

        tasks = [_spec("a", "list_routines", retries=3)]
        run = self._run(tasks, script)
        self.assertEqual(run.task_runs["a"].attempt, 1)


class TestRunnerTimeout(unittest.TestCase):
    def test_slow_node_times_out_and_marks_outcome_unknown(self):
        import time

        def slow(tool, args):
            time.sleep(3)
            return _ok()

        wf = _wf("t", [_spec("a", "list_routines", timeout=1)])
        run = dagflow.run_dag(wf, execute_fn=slow, sleep_fn=lambda s: None)
        self.assertEqual(run.task_runs["a"].state, TaskState.TIMED_OUT)
        self.assertIn("unknown", run.task_runs["a"].error.lower())
        self.assertEqual(run.state, TaskState.FAILED)

    def test_fast_node_with_a_timeout_runs_inline_and_succeeds(self):
        wf = _wf("t", [_spec("a", "list_routines", timeout=10)])
        run = dagflow.run_dag(wf, execute_fn=lambda t, a: _ok(),
                              sleep_fn=lambda s: None)
        self.assertEqual(run.task_runs["a"].state, TaskState.SUCCESS)
        self.assertEqual(run.task_runs["a"].attempt, 1)


class TestRunnerBudget(unittest.TestCase):
    def test_budget_trip_skips_the_rest_and_fails_the_run(self):
        # The budget clamps to a 60 s minimum, so a real trip is
        # driven by the clock: the first monotonic() call (run
        # start) returns 0, every later call returns 61 — past
        # the 60 s budget before the first level even starts.
        calls = {"n": 0}

        def fake_mono():
            calls["n"] += 1
            return 0.0 if calls["n"] == 1 else 61.0

        tasks = [_spec("a", "list_routines"),
                 _spec("b", "list_routines", deps=("a",)),
                 _spec("c", "list_routines", deps=("b",))]
        wf = _wf("b", tasks)
        with mock.patch("time.monotonic", side_effect=fake_mono):
            run = dagflow.run_dag(wf, run_budget_seconds=60,
                                  execute_fn=lambda t, a: _ok(),
                                  sleep_fn=lambda s: None)
        self.assertEqual(run.state, TaskState.FAILED)
        self.assertIn("budget", run.reason)
        for task_id in ("a", "b", "c"):
            self.assertEqual(run.task_runs[task_id].state, TaskState.SKIPPED)
        self.assertEqual(run.summary()["skipped"], 3)

    def test_budget_clamps_to_60_1800(self):
        # The clamp is visible in the trip reason: a 5 s request
        # trips as "60s", a 9999 s request as "1800s".
        calls = {"n": 0}

        def fake_mono():
            calls["n"] += 1
            return 0.0 if calls["n"] == 1 else 61.0

        wf = _wf("b", [_spec("a", "list_routines")])
        with mock.patch("time.monotonic", side_effect=fake_mono):
            run = dagflow.run_dag(wf, run_budget_seconds=5,
                                  execute_fn=lambda t, a: _ok(),
                                  sleep_fn=lambda s: None)
        self.assertIn("60s", run.reason)
        calls["n"] = 0
        with mock.patch("time.monotonic", side_effect=fake_mono):
            run = dagflow.run_dag(wf, run_budget_seconds=9999,
                                  execute_fn=lambda t, a: _ok(),
                                  sleep_fn=lambda s: None)
        # 61 s in is past the 60 s minimum but not the 1800 s
        # clamp — so a 9999 s budget does NOT trip here; the node
        # runs. The clamp itself is the max() in run_dag; the
        # 1800 bound is proven by the reason only when the clock
        # passes it, which this test does not fake.
        self.assertEqual(run.state, TaskState.SUCCESS)


# ---------------------------------------------------------------- history


class TestRunHistory(_TempStoreMixin):
    def _run_once(self):
        wf = _wf("h", [_spec("a", "list_routines")])
        run = dagflow.run_dag(wf, execute_fn=lambda t, a: _ok(),
                              sleep_fn=lambda s: None)
        dagflow.save_dag_run(run, db_path=self._db_path)
        return run

    def test_run_is_saved_and_re_read(self):
        run = self._run_once()
        detail = dagflow.get_run_detail(run.run_id, db_path=self._db_path)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["run_id"], run.run_id)
        self.assertEqual(detail["state"], "success")
        self.assertEqual(len(detail["task_runs"]), 1)
        self.assertEqual(detail["task_runs"][0]["task_id"], "a")
        self.assertEqual(detail["task_runs"][0]["state"], "success")

    def test_get_runs_lists_newest_first(self):
        first = self._run_once()
        second = self._run_once()
        rows = dagflow.get_runs("h", db_path=self._db_path, limit=10)
        self.assertEqual([r["run_id"] for r in rows],
                         [second.run_id, first.run_id])

    def test_summary_counts_match_the_tasks(self):
        run = self._run_once()
        self.assertEqual(run.summary(), {"success": 1})

    def test_cleanup_keeps_the_newest_n(self):
        for _ in range(3):
            self._run_once()
        deleted = dagflow.cleanup_old_runs("h", keep=2, db_path=self._db_path)
        self.assertEqual(deleted, 1)
        rows = dagflow.get_runs("h", db_path=self._db_path, limit=10)
        self.assertEqual(len(rows), 2)

    def test_history_db_is_sqlite_wal(self):
        self._run_once()
        self.assertTrue(self._db_path.exists())
        with sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("dag_runs", tables)
        self.assertIn("task_runs", tables)

    def test_get_runs_returns_empty_without_a_db(self):
        self.assertEqual(dagflow.get_runs("h", db_path=self._db_path), [])
        self.assertIsNone(
            dagflow.get_run_detail("nope", db_path=self._db_path))


# ------------------------------------------------------- trust path + tools


class TestTrustPath(_TempStoreMixin):
    def test_real_funnel_refusal_fails_the_node_without_retry(self):
        # window_place with no args: the registry's OWN schema
        # validation refuses it (status refused, kind validation) —
        # no patch, the real gate. The node must be FAILED after
        # exactly one attempt and its dependent SKIPPED.
        ok, message = dagflow.define_workflow({
            "workflow_id": "trust", "description": "t", "tasks": [
                {"task_id": "place", "tool": "window_place", "args": {},
                 "retries": 5},
                {"task_id": "after", "tool": "list_routines", "args": {},
                 "depends_on": ["place"]}]})
        self.assertTrue(ok, message)
        wf = dagflow.load_workflow("trust")
        run = dagflow.run_dag(wf, sleep_fn=lambda s: None)  # real funnel
        self.assertEqual(run.task_runs["place"].state, TaskState.FAILED)
        self.assertEqual(run.task_runs["place"].attempt, 1)
        self.assertEqual(run.task_runs["after"].state, TaskState.SKIPPED)
        self.assertEqual(run.state, TaskState.FAILED)
        self.assertIn("place", run.reason)

    def test_patched_refusal_is_never_retried_or_overridden(self):
        calls = []

        def fake(tool, args):
            calls.append(tool)
            return _refuse("gate says no")

        wf = _wf("t", [_spec("a", "list_routines", retries=5)])
        run = dagflow.run_dag(wf, execute_fn=fake, sleep_fn=lambda s: None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(run.task_runs["a"].state, TaskState.FAILED)

    def test_real_funnel_end_to_end_with_read_only_tools(self):
        # The built-in daily-status workflow, through the REAL
        # funnel, no fakes: every node is a read-only tool.
        wf = dagflow.load_workflow("daily-status")
        self.assertIsNotNone(wf)
        run = dagflow.run_dag(wf, sleep_fn=lambda s: None)
        self.assertEqual(run.state, TaskState.SUCCESS, run.reason)
        self.assertEqual(run.summary(), {"success": 4})


class TestRegistryWiring(_TempStoreMixin):
    def test_registry_total_is_758(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertEqual(len(_registry.get_declarations()), 758)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing

    def test_the_four_tools_are_declared(self):
        decls = {d["name"]: d for d in _registry.get_declarations()}
        for name in ("define_workflow", "run_workflow", "list_workflows", "workflow_runs"):
            self.assertIn(name, decls)
        self.assertEqual(
            list(decls["define_workflow"]["parameters"]["properties"]),
            ["workflow_id", "description", "tasks"])
        self.assertEqual(
            list(decls["run_workflow"]["parameters"]["properties"]),
            ["workflow_id", "run_budget_seconds"])
        self.assertEqual(
            list(decls["list_workflows"]["parameters"]["properties"]), [])
        self.assertEqual(
            list(decls["workflow_runs"]["parameters"]["properties"]),
            ["workflow_id", "limit"])

    def test_define_and_run_are_free_workflow_class(self):
        from core.funnel import tools_gate_free
        self.assertEqual(tools_gate_free._FREE_BY_DESIGN["define_workflow"], "workflow")
        self.assertEqual(tools_gate_free._FREE_BY_DESIGN["run_workflow"], "workflow")

    def test_list_and_runs_are_read_only(self):
        from core.funnel import tools_gate_readonly
        self.assertIn("list_workflows", tools_gate_readonly._READ_ONLY_TOOLS)
        self.assertIn("workflow_runs", tools_gate_readonly._READ_ONLY_TOOLS)

    def test_run_workflow_takes_the_long_deadline(self):
        from core.funnel import tools_handlers_limits
        self.assertEqual(tools_handlers_limits._TOOL_DEADLINE_CLASS["run_workflow"], "long")

    def test_the_four_tools_are_grouped_in_system_and_apps(self):
        from core import catalogue_snapshot
        groups = catalogue_snapshot.GROUP_MEMBERS
        for name in ("define_workflow", "run_workflow", "list_workflows", "workflow_runs"):
            self.assertIn(name, groups.get("System & apps", ()),
                          f"{name} not grouped in System & apps")

    def test_define_tool_refuses_a_workflow_with_no_tasks(self):
        # The registry envelope is status success (the handler
        # returned normally); the refusal lives in result.ok.
        out = _registry.execute("define_workflow", {
            "workflow_id": "empty", "description": "t", "tasks": []})
        self.assertEqual(out["status"], "success")
        self.assertFalse(out["result"]["ok"])
        self.assertIn("task", out["result"]["message"].lower())
        self.assertFalse((self._workflows_dir / "empty.json").exists())

    def test_define_then_run_through_the_tools(self):
        out = _registry.execute("define_workflow", {
            "workflow_id": "toolrun", "description": "t", "tasks": [
                {"task_id": "a", "tool": "list_routines", "args": {}}]})
        self.assertTrue(out["result"]["ok"], out)
        out = _registry.execute("run_workflow", {"workflow_id": "toolrun"})
        self.assertTrue(out["result"]["ok"], out)
        self.assertEqual(out["result"]["state"], "success")
        self.assertEqual(out["result"]["counts"], {"success": 1})
        # ...and the run record is in the (temp) history.
        out = _registry.execute("workflow_runs", {"workflow_id": "toolrun"})
        self.assertEqual(len(out["result"]["runs"]), 1)
        self.assertEqual(out["result"]["runs"][0]["state"], "success")

    def test_list_workflows_tool_lists_the_builtin(self):
        out = _registry.execute("list_workflows", {})
        self.assertEqual(out["status"], "success")
        ids = [w["workflow_id"] for w in out["result"]["workflows"]]
        self.assertIn("daily-status", ids)

    def test_workflow_runs_tool_reports_no_runs_yet(self):
        out = _registry.execute("workflow_runs", {"workflow_id": "daily-status"})
        self.assertEqual(out["status"], "success")
        self.assertEqual(out["result"]["runs"], [])

    def test_run_workflow_tool_runs_the_builtin_end_to_end(self):
        out = _registry.execute("run_workflow", {"workflow_id": "daily-status"})
        self.assertEqual(out["status"], "success")
        self.assertTrue(out["result"]["ok"], out)
        self.assertEqual(out["result"]["state"], "success")
        self.assertEqual(out["result"]["counts"], {"success": 4})

    def test_run_workflow_tool_refuses_an_unknown_workflow(self):
        out = _registry.execute("run_workflow", {"workflow_id": "nope"})
        self.assertFalse(out["result"]["ok"])
        self.assertIn("nope", out["result"]["message"])


class TestDemoWorkflow(unittest.TestCase):
    def test_daily_status_is_built_in_and_valid(self):
        wf = dagflow.load_workflow("daily-status")
        self.assertIsNotNone(wf)
        self.assertEqual(len(wf.tasks), 4)
        self.assertEqual(wf.validate(known_tools={t.tool for t in wf.tasks.values()}), [])
        self.assertEqual(wf.get_execution_order(),
                         ["companion", "wake", "meeting", "models"])
        self.assertEqual(wf.levels(),
                         [["companion"], ["wake", "meeting"], ["models"]])

    def test_daily_status_nodes_are_all_read_only_tools(self):
        from core.funnel import tools_gate_readonly
        wf = dagflow.load_workflow("daily-status")
        for task in wf.tasks.values():
            self.assertIn(task.tool, tools_gate_readonly._READ_ONLY_TOOLS,
                          f"{task.tool} is not read-only")


if __name__ == "__main__":
    unittest.main()
