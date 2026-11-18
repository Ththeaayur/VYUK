"""GAPS-12 suite: the MCP branch runs the ONE funnel, and Rule of Two
fails CLOSED.

Mirrors prove_mcp_funnel.py's live checks as unit tests. A stub MCP
bus (same _FakeBus shape as tests/test_logicL2.py), a patched
mcp_policy.policy_class (the operator-reviewed class, per tool name),
and a patched confirm_gate where an answer is needed. No real MCP
server, no real handler side effect: the native Rule-of-Two test
stubs the acronym_finder handler, and every patch is restored.

Determinism notes: setUp resets the Rule-of-Two session budget and
clears session taint, so earlier suites in the same process cannot
leak legs/taint into these checks. The timeout test uses a
per-process tool identity — a timeout leaves an unknown-outcome
receipt and a cool-down keyed on tool name + args, and the funnel's
own retry guard would refuse an identical call on the next run.
"""

import os
import unittest

import core.confirm_gate as confirm_gate
import core.injection as injection
import core.mcp_client as mcp_client
import core.mcp_policy as mcp_policy
from core.funnel import rule_of_two as rot_mod
from core.funnel.tools_registry import ToolRegistry, tools


class _FakeBus:
    """Minimal MCP bus (A2/A3 pattern) with dispatch recording."""

    def __init__(self, entries, behaviour="ok"):
        self._entries = {e["name"]: e for e in entries}
        self.dispatched = []
        self.behaviour = behaviour

    def resolve(self, name):
        return self._entries.get(name)

    def tool_changed(self, name):
        return False

    def ensure_server(self, server):
        return True, None

    def call_tool_sync(self, server, tool, args, timeout=120.0):
        self.dispatched.append((server, tool))
        if self.behaviour == "timeout":
            return False, "timed out", "timeout"
        return True, "fake mcp result", None


def _entry(name, tool, parameters=None, description="probe"):
    return {"name": name, "server": name.split("__")[1],
            "tool": tool, "annotations": {},
            "parameters": parameters if parameters is not None else
            {"type": "object", "properties": {}},
            "description": description}


class TestUnifiedFunnel(unittest.TestCase):
    def setUp(self):
        self._orig_get_bus = mcp_client.get_bus
        self._orig_policy_class = mcp_policy.policy_class
        self._orig_has_handler = confirm_gate.has_handler
        self._orig_request = confirm_gate.request_confirmation
        self._classes = {}
        try:
            injection.clear_taint(force=True)
        except Exception:  # noqa: BLE001 — determinism is best-effort
            pass
        rot_mod.reset_for_tests()

    def tearDown(self):
        mcp_client.get_bus = self._orig_get_bus
        mcp_policy.policy_class = self._orig_policy_class
        confirm_gate.has_handler = self._orig_has_handler
        confirm_gate.request_confirmation = self._orig_request
        rot_mod.reset_for_tests()

    def _install(self, bus):
        mcp_client.get_bus = lambda: bus
        mcp_policy.policy_class = lambda entry: self._classes.get(
            entry.get("tool"), "confirm")

    def test_read_runs_native_stages_and_dispatches(self):
        self._classes["probe"] = "read"
        bus = _FakeBus([_entry("mcp__l2srv__probe", "probe")])
        self._install(bus)
        stages = ["_exec_validate", "_exec_failsafe", "_exec_killchain",
                  "_exec_confirm", "_exec_rule_of_two", "_exec_limits",
                  "_exec_dispatch"]
        seen = []
        origs = {}

        def _wrap(name):
            orig = getattr(ToolRegistry, name)
            origs[name] = orig

            def _spied(self, ctx, _orig=orig, _name=name):
                seen.append(_name)
                return _orig(self, ctx)

            setattr(ToolRegistry, name, _spied)

        for s in stages:
            _wrap(s)
        try:
            result = tools.execute("mcp__l2srv__probe", {})
        finally:
            for name, orig in origs.items():
                setattr(ToolRegistry, name, orig)
        self.assertEqual(result.get("status"), "success", result)
        self.assertEqual(bus.dispatched, [("l2srv", "probe")])
        for s in stages:
            self.assertIn(s, seen, f"native stage {s} did not run")

    def test_confirm_deny_refuses_without_dispatch(self):
        self._classes["probe"] = "confirm"
        bus = _FakeBus([_entry("mcp__l2srv__probe", "probe")])
        self._install(bus)
        confirm_gate.has_handler = lambda: True
        confirm_gate.request_confirmation = lambda *a, **k: False
        result = tools.execute("mcp__l2srv__probe", {})
        self.assertEqual(result.get("kind"), "not_confirmed", result)
        self.assertEqual(bus.dispatched, [])

    def test_confirm_approve_dispatches(self):
        self._classes["probe"] = "confirm"
        bus = _FakeBus([_entry("mcp__l2srv__probe", "probe")])
        self._install(bus)
        confirm_gate.has_handler = lambda: True
        confirm_gate.request_confirmation = lambda *a, **k: True
        result = tools.execute("mcp__l2srv__probe", {})
        self.assertEqual(result.get("status"), "success", result)
        self.assertEqual(bus.dispatched, [("l2srv", "probe")])

    def test_money_name_needs_confirmation_even_read_class(self):
        self._classes["pay"] = "read"
        bus = _FakeBus([_entry("mcp__l2srv__pay", "pay")])
        self._install(bus)
        result = tools.execute("mcp__l2srv__pay", {})
        self.assertEqual(result.get("kind"), "not_confirmed", result)
        self.assertEqual(bus.dispatched, [])

    def test_native_rule_of_two_failure_refuses(self):
        ran = []
        orig_handler = tools._tools["acronym_finder"]["handler"]
        tools._tools["acronym_finder"]["handler"] = (
            lambda **kw: ran.append(True) or "stubbed")
        orig_legs = rot_mod.legs_for_native_call

        def _boom(*a, **k):
            raise RuntimeError("boom")

        rot_mod.legs_for_native_call = _boom
        try:
            result = tools.execute("acronym_finder", {})
        finally:
            rot_mod.legs_for_native_call = orig_legs
            tools._tools["acronym_finder"]["handler"] = orig_handler
        self.assertEqual(result.get("kind"), "refused", result)
        self.assertEqual(result.get("status"), "refused", result)
        self.assertEqual(ran, [])

    def test_mcp_rule_of_two_failure_refuses(self):
        self._classes["probe"] = "read"
        bus = _FakeBus([_entry("mcp__l2srv__probe", "probe")])
        self._install(bus)
        orig_legs = rot_mod.legs_for_mcp_call

        def _boom(*a, **k):
            raise RuntimeError("boom")

        rot_mod.legs_for_mcp_call = _boom
        try:
            result = tools.execute("mcp__l2srv__probe", {})
        finally:
            rot_mod.legs_for_mcp_call = orig_legs
        self.assertEqual(result.get("kind"), "refused", result)
        self.assertEqual(result.get("status"), "refused", result)
        self.assertEqual(bus.dispatched, [])

    def test_malformed_schema_refuses_invalid_schema(self):
        self._classes["probe"] = "read"
        bad_params = {"type": "object",
                      "properties": {"v": {"type": 12345}}}
        bus = _FakeBus([_entry("mcp__l2srv__probe", "probe",
                               parameters=bad_params)])
        self._install(bus)
        result = tools.execute("mcp__l2srv__probe", {"v": 1})
        self.assertEqual(result.get("kind"), "invalid_schema", result)
        self.assertEqual(result.get("status"), "refused", result)
        self.assertEqual(bus.dispatched, [])

    def test_bad_arguments_refuse_validation(self):
        self._classes["probe"] = "read"
        params = {"type": "object",
                  "properties": {"v": {"type": "integer"}},
                  "required": ["v"]}
        bus = _FakeBus([_entry("mcp__l2srv__probe", "probe",
                               parameters=params)])
        self._install(bus)
        result = tools.execute("mcp__l2srv__probe", {"v": "three"})
        self.assertEqual(result.get("kind"), "validation", result)
        self.assertEqual(bus.dispatched, [])

    def test_unknown_mcp_tool_is_unavailable(self):
        bus = _FakeBus([])
        self._install(bus)
        result = tools.execute("mcp__ghost__probe", {})
        self.assertEqual(result.get("kind"), "unavailable", result)
        self.assertEqual(bus.dispatched, [])

    def test_timeout_is_outcome_unknown_with_operation_id(self):
        suffix = f"tmo-{os.getpid()}"
        tool = f"probe-{suffix}"
        server = f"l2srv-{suffix}"
        name = f"mcp__{server}__{tool}"
        self._classes[tool] = "read"
        bus = _FakeBus([_entry(name, tool)], behaviour="timeout")
        self._install(bus)
        result = tools.execute(name, {})
        self.assertEqual(result.get("status"), "timeout", result)
        self.assertEqual(result.get("outcome"), "unknown", result)
        self.assertTrue(result.get("operation_id"), result)


if __name__ == "__main__":
    unittest.main()
