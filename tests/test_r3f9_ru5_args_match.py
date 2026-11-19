"""Round-3 fix #9 (A-RU5): plan-step auto-complete matches arguments.

Before the fix, `observe_tool_execution` matched tool-name + success
ONLY, so one unrelated `get_current_time_date` call completed both
"Check the current time" and "Check the time in Tokyo". The seam now
takes the executed call's args: a step declaring non-empty `args`
completes only when every declared key is present with an equal value
in the call (extra call args are fine — subset, not exact). Steps
declaring no args keep the old behaviour (B12 intent, the
test_routerfinish pins).
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.brain import planner, planner_checkpoint, planner_plans
from core.voice import live_client

ArgsForCall = live_client.GeminiLiveSession._observe_args_for_call
NameForCall = live_client.GeminiLiveSession._observe_name_for_call

_OK = {"status": "success", "result": "2026-11-19 22:00 Asia/Tokyo"}


class ArgsMatchSeamTests(unittest.TestCase):
    """The seam rule itself, via observe_tool_execution directly."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="r3f9_ru5_plans_"))
        self._orig_plans = planner._PLANS_PATH
        planner._PLANS_PATH = self._tmp / "plans.json"
        planner_plans._PLANS_PATH = planner._PLANS_PATH
        planner_checkpoint._PLANS_PATH = planner._PLANS_PATH

    def tearDown(self):
        planner._PLANS_PATH = self._orig_plans
        planner_plans._PLANS_PATH = self._orig_plans
        planner_checkpoint._PLANS_PATH = self._orig_plans

    def _write_plans(self, steps):
        plans = [{"id": "p1", "steps": steps}]
        (self._tmp / "plans.json").write_text(
            json.dumps(plans), encoding="utf-8")

    def _step(self, index=0):
        plans, _damaged = planner._load_plans()
        return plans[0]["steps"][index]

    def _observe(self, name, result, args):
        return planner.observe_tool_execution(name, result, args)

    def test_matching_args_complete_the_step(self):
        self._write_plans([{
            "text": "Check the time in Tokyo",
            "tool": "get_current_time_date",
            "args": {"timezone": "Asia/Tokyo"}, "status": "pending"}])
        anns = self._observe("get_current_time_date", _OK,
                             {"timezone": "Asia/Tokyo"})
        self.assertTrue(anns)
        self.assertEqual(self._step()["status"], "done")

    def test_mismatched_args_leave_the_step_pending(self):
        self._write_plans([{
            "text": "Check the time in Tokyo",
            "tool": "get_current_time_date",
            "args": {"timezone": "Asia/Tokyo"}, "status": "pending"}])
        anns = self._observe(
            "get_current_time_date",
            {"status": "success", "result": "2026-11-19 13:00 Asia/Calcutta"},
            {})
        self.assertEqual(anns, [])
        self.assertEqual(self._step()["status"], "pending")

    def test_extra_call_args_are_fine(self):
        # Subset match, not exact: the call may carry more args.
        self._write_plans([{
            "text": "Check the time in Tokyo",
            "tool": "get_current_time_date",
            "args": {"timezone": "Asia/Tokyo"}, "status": "pending"}])
        anns = self._observe("get_current_time_date", _OK,
                             {"timezone": "Asia/Tokyo", "format": "24h"})
        self.assertTrue(anns)
        self.assertEqual(self._step()["status"], "done")

    def test_arg_less_step_keeps_old_behaviour(self):
        # No declared args: any successful call of the tool
        # completes the step, exactly as before the fix (B12).
        self._write_plans([{
            "text": "Check the current time",
            "tool": "get_current_time_date",
            "status": "pending"}])
        anns = self._observe(
            "get_current_time_date",
            {"status": "success", "result": "2026-11-19 13:00 Asia/Calcutta"},
            {})
        self.assertTrue(anns)
        self.assertEqual(self._step()["status"], "done")

    def test_empty_declared_args_keep_old_behaviour(self):
        self._write_plans([{
            "text": "Check the current time",
            "tool": "get_current_time_date",
            "args": {}, "status": "pending"}])
        anns = self._observe(
            "get_current_time_date",
            {"status": "success", "result": "2026-11-19 13:00 Asia/Calcutta"},
            {"timezone": "Asia/Calcutta"})
        self.assertTrue(anns)
        self.assertEqual(self._step()["status"], "done")

    def test_check_gate_still_applies_with_matching_args(self):
        # Args match but the B12 check fails: stays pending.
        self._write_plans([{
            "text": "Find secret files", "tool": "find_files",
            "args": {"pattern": "*.secret"}, "status": "pending",
            "check": {"contains": "secret"}}])
        anns = self._observe(
            "find_files",
            {"status": "success", "result": "3 files: notes.txt"},
            {"pattern": "*.secret"})
        self.assertEqual(anns, [])
        self.assertEqual(self._step()["status"], "pending")

    def test_check_gate_and_args_both_pass_complete(self):
        self._write_plans([{
            "text": "Find secret files", "tool": "find_files",
            "args": {"pattern": "*.secret"}, "status": "pending",
            "check": {"contains": "secret"}}])
        anns = self._observe(
            "find_files",
            {"status": "success", "result": "1 file: my.secret.bak"},
            {"pattern": "*.secret"})
        self.assertTrue(anns)
        self.assertEqual(self._step()["status"], "done")

    def test_no_args_supplied_never_completes_an_arg_declaring_step(self):
        # Old-style callers pass args=None: an arg-declaring step
        # must stay pending (its match cannot be proven); an
        # arg-less step still completes.
        self._write_plans([
            {"text": "Check the time in Tokyo",
             "tool": "get_current_time_date",
             "args": {"timezone": "Asia/Tokyo"}, "status": "pending"},
            {"text": "Check the current time",
             "tool": "get_current_time_date",
             "status": "pending"}])
        anns = self._observe("get_current_time_date", _OK, None)
        self.assertEqual(len(anns), 1)
        self.assertEqual(self._step(0)["status"], "pending")
        self.assertEqual(self._step(1)["status"], "done")

    def test_failed_execution_completes_nothing_even_with_matching_args(self):
        self._write_plans([{
            "text": "Check the time in Tokyo",
            "tool": "get_current_time_date",
            "args": {"timezone": "Asia/Tokyo"}, "status": "pending"}])
        anns = self._observe(
            "get_current_time_date",
            {"status": "failure", "result": "refused"},
            {"timezone": "Asia/Tokyo"})
        self.assertEqual(anns, [])
        self.assertEqual(self._step()["status"], "pending")


class ObserveArgsForCallTests(unittest.TestCase):
    """The live client's observed-args helper (routed calls)."""

    def _fc(self, name, args):
        return SimpleNamespace(name=name, args=dict(args))

    def test_direct_call_observes_its_own_args(self):
        fc = self._fc("get_current_time_date", {"timezone": "Asia/Tokyo"})
        self.assertEqual(ArgsForCall(fc, "get_current_time_date"),
                         {"timezone": "Asia/Tokyo"})

    def test_routed_call_observes_inner_args(self):
        fc = self._fc("route_system_apps",
                      {"tool": "feel_orb_map", "args": {"level": 0.5}})
        name = NameForCall(fc, {"status": "success", "result": "ok"})
        self.assertEqual(name, "feel_orb_map")
        self.assertEqual(ArgsForCall(fc, name), {"level": 0.5})

    def test_routed_call_with_json_string_bundle_parses_it(self):
        # Live models serialize the inner bundle as a JSON string;
        # the funnel parses it before execution, and so must the
        # observer — otherwise a routed step could never match.
        fc = self._fc("use_toolbox",
                      {"action": "call", "tool": "feel_orb_map",
                       "args": '{"level": 0.5}'})
        name = NameForCall(fc, {"status": "success", "result": "ok"})
        self.assertEqual(name, "feel_orb_map")
        self.assertEqual(ArgsForCall(fc, name), {"level": 0.5})

    def test_failed_inner_call_falls_back_to_dispatcher_args(self):
        # Inner call failed: observed under the dispatcher name
        # with the dispatcher's own args (no step declares a
        # dispatcher name, so nothing completes either way).
        fc = self._fc("route_system_apps",
                      {"tool": "feel_orb_map", "args": {"level": 0.5}})
        name = NameForCall(fc, {"status": "failure", "result": "nope"})
        self.assertEqual(name, "route_system_apps")
        self.assertEqual(
            ArgsForCall(fc, name),
            {"tool": "feel_orb_map", "args": {"level": 0.5}})

    def test_routed_call_without_inner_args_observes_empty(self):
        fc = self._fc("route_system_apps", {"tool": "feel_orb_map"})
        name = NameForCall(fc, {"status": "success", "result": "ok"})
        self.assertEqual(name, "feel_orb_map")
        self.assertEqual(ArgsForCall(fc, name), {})


if __name__ == "__main__":
    unittest.main()
