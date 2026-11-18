"""Tests for the router finishing build (working copy, 2026-10-27).

Item 1 — B12 planner observation names the INNER tool for routed
calls: live_client.GeminiLiveSession._observe_name_for_call maps a
route_* call to its args.tool (and keeps the use_toolbox mapping
verbatim), and a plan step declaring the inner tool completes
under that observation, while observing under the router name
completes nothing.

Item 2 — the executor safety-budget trip payload carries ok=False:
driving the native execute() path with a pre-filled budget
window (timestamps only — thresholds, window, and trip logic
untouched) returns status error / kind budget / ok False, so
standard result handling that reads ["ok"] consumes it cleanly
instead of raising KeyError 'ok'.
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.voice import live_client  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.brain import planner  # Phase 3 B2-remainder INTENTIONAL: planner moved to core/brain/
from core.brain import planner_checkpoint, planner_plans  # INTENTIONAL (skills split B41): impl modules for dual-patching (see setUp notes below)
from core.funnel import tools_handlers  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

NameForCall = live_client.GeminiLiveSession._observe_name_for_call


def _call(name, args=None, result=None):
    return SimpleNamespace(name=name, args=dict(args or {})), result


class ObserveNameTests(unittest.TestCase):
    def test_direct_call_observes_under_own_name(self):
        fc, result = _call("feel_orb_map", {"level": 0.5},
                           {"status": "success", "result": "ORB"})
        self.assertEqual(NameForCall(fc, result), "feel_orb_map")

    def test_routed_call_observes_under_inner_tool(self):
        fc, result = _call("route_system_apps",
                           {"tool": "feel_orb_map", "args": {"level": 0.5}},
                           {"status": "success", "result": "ORB"})
        self.assertEqual(NameForCall(fc, result), "feel_orb_map")

    def test_routed_call_without_tool_observes_under_router(self):
        fc, result = _call("route_system_apps", {},
                           {"status": "success", "result": "ORB"})
        self.assertEqual(NameForCall(fc, result), "route_system_apps")

    def test_routed_call_with_failed_inner_observes_under_router(self):
        fc, result = _call("route_system_apps",
                           {"tool": "feel_orb_map", "args": {}},
                           {"status": "error",
                            "result": "did not run: unknown tool"})
        self.assertEqual(NameForCall(fc, result), "route_system_apps")

    def test_use_toolbox_call_mapping_preserved(self):
        fc, result = _call("use_toolbox",
                           {"action": "call", "tool": "feel_orb_map"},
                           {"status": "success", "result": "ORB"})
        self.assertEqual(NameForCall(fc, result), "feel_orb_map")

    def test_use_toolbox_failed_call_falls_back_to_dispatcher(self):
        fc, result = _call("use_toolbox",
                           {"action": "call", "tool": "feel_orb_map"},
                           {"status": "success",
                            "result": "did not run: no such tool"})
        self.assertEqual(NameForCall(fc, result), "use_toolbox")

    def test_use_toolbox_non_call_action_observes_under_dispatcher(self):
        fc, result = _call("use_toolbox",
                           {"action": "describe", "tool": "feel_orb_map"},
                           {"status": "success", "result": "schema"})
        self.assertEqual(NameForCall(fc, result), "use_toolbox")


class RoutedObservationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="routerfinish_plans_"))
        self._orig_plans = planner._PLANS_PATH
        planner._PLANS_PATH = self._tmp / "plans.json"
        # INTENTIONAL (skills split B41): _PLANS_PATH is bound in the
        # plans + checkpoint modules too (snapshot imports); patch every
        # binding with the same value, or moved callers would miss it.
        planner_plans._PLANS_PATH = planner._PLANS_PATH
        planner_checkpoint._PLANS_PATH = planner._PLANS_PATH

    def tearDown(self):
        planner._PLANS_PATH = self._orig_plans
        planner_plans._PLANS_PATH = self._orig_plans
        planner_checkpoint._PLANS_PATH = self._orig_plans

    def _write_plan(self):
        plan = [{"id": "rf1",
                 "steps": [{"tool": "feel_orb_map", "status": "pending",
                            "text": "map the orb"}]}]
        (self._tmp / "plans.json").write_text(
            json.dumps(plan), encoding="utf-8")

    def _read_step(self):
        plans, _damaged = planner._load_plans()
        return plans[0]["steps"][0]

    def test_routed_observation_completes_inner_step(self):
        self._write_plan()
        fc, result = _call("route_system_apps",
                           {"tool": "feel_orb_map", "args": {"level": 0.5}},
                           {"status": "success",
                            "result": "ORB_MAPPING scale=1.1"})
        name = NameForCall(fc, result)
        self.assertEqual(name, "feel_orb_map")
        anns = planner.observe_tool_execution(name, result)
        self.assertTrue(anns, "inner step should auto-complete")
        self.assertEqual(self._read_step()["status"], "done")

    def test_router_name_observation_completes_nothing(self):
        self._write_plan()
        result = {"status": "success",
                  "result": "ORB_MAPPING scale=1.1"}
        anns = planner.observe_tool_execution("route_system_apps", result)
        self.assertEqual(anns, [])
        self.assertEqual(self._read_step()["status"], "pending")


class BudgetTripPayloadTests(unittest.TestCase):
    def setUp(self):
        self._saved = list(tools_handlers._budget_timestamps)
        self._budget = tools_handlers._tool_call_budget()
        now = tools_handlers._budget_now()
        tools_handlers._budget_timestamps[:] = \
            [now] * (self._budget + 10)

    def tearDown(self):
        tools_handlers._budget_timestamps[:] = self._saved

    def test_tripped_payload_carries_ok_false(self):
        out = tools.execute("get_current_time_date", {})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
        self.assertEqual(out.get("status"), "refused")
        self.assertEqual(out.get("kind"), "budget")
        # This line raised KeyError 'ok' before the fix: the
        # tripped payload carried no 'ok' key at all.
        self.assertIs(out["ok"], False)
        self.assertIn("message", out)

    def test_tripped_payload_consumed_by_result_style_handling(self):
        out = tools.execute("get_current_time_date", {})
        # Mirrors the funnel tests' _result helper: unwrap a
        # "result" key when present, else take the dict as-is.
        data = out["result"] if "result" in out else out
        self.assertIs(data["ok"], False)

    def test_budget_threshold_untouched(self):
        self.assertEqual(tools_handlers._tool_call_budget(), self._budget)


if __name__ == "__main__":
    unittest.main()
