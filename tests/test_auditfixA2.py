"""Audit fix A2 — fail-closed guards (master audit P0-2, P1-1).

Mocks / fault injection only: no real sending, no real money
movement, no network. Every consult failure below is injected
by monkeypatching a consult to raise; the test then asserts
the action is DENIED (sensitive/mutating) or proceeds only
under the documented read exemption — never silently waved
through, and never treated as "disabled".

The deny rule (see core/funnel/guard_state.py and the A2 comments
at each call site):
* money guard UNAVAILABLE -> deny (confirm ask returns
  False; the funnel forces the confirm ask, which denies).
* failsafe UNAVAILABLE -> non-read refused, reads exempt
  (reads were already exempt when tripped).
* kill-chain gate UNAVAILABLE -> refused unless the call is
  read-class AND provably local (native: read AND NOT
  _is_egress_read — the Egress batch corrected the old
  inverted mapping, which had let network reads through
  while refusing local ones; MCP: read, since read <=>
  non-egress there). Rung 5's full stop is the documented residual:
  an unreadable consult cannot prove rung 5, so pure reads
  still run for triage visibility.
* budget UNAVAILABLE -> mutating refused; reads proceed
  with a printed limiter-unavailable note, unrecorded.
* explicitly disabled (budget limit configured to 0) ->
  proceeds, records nothing. Only declared config disables;
  no exception path sets "disabled".
"""

import tempfile
import unittest
from pathlib import Path

from core import confirm_gate, failsafe  # Phase 3 F2b INTENTIONAL: kill_chain split to its own funnel import below
from core.funnel import kill_chain  # Phase 3 F2b INTENTIONAL: kill_chain moved to core/funnel/
from core.funnel import money_guard as money_guard  # Phase 3 F2b INTENTIONAL: money_guard moved to core/funnel/
from core.funnel import tools_gate as tools_gate_mod  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel import tools_handlers as tools_handlers_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel import tools_registry as tools_registry_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel.guard_state import FailsafeState, GuardState  # Phase 3 F2b INTENTIONAL: guard_state moved to core/funnel/


def _raise(exc_type=RuntimeError):
    def _f(*args, **kwargs):
        raise exc_type("injected consult failure")

    return _f


class _A2Base(unittest.TestCase):
    def setUp(self):
        self._orig_log = confirm_gate._log_decision
        confirm_gate._log_decision = lambda *a, **k: None
        confirm_gate.clear_handler()
        # Budget state is process-global: snapshot defensively.
        self._orig_budget_now = tools_handlers_mod._budget_now
        self._orig_budget_fn = tools_handlers_mod._tool_call_budget
        self._orig_timestamps = list(tools_handlers_mod._budget_timestamps)
        self._orig_failsafe_tripped = failsafe.tripped
        self._orig_gate_tool = kill_chain.gate_tool
        self._orig_mg_check = money_guard.check

    def tearDown(self):
        confirm_gate._log_decision = self._orig_log
        confirm_gate.clear_handler()
        tools_handlers_mod._budget_now = self._orig_budget_now
        tools_handlers_mod._tool_call_budget = self._orig_budget_fn
        tools_handlers_mod._budget_timestamps[:] = self._orig_timestamps
        failsafe.tripped = self._orig_failsafe_tripped
        kill_chain.gate_tool = self._orig_gate_tool
        money_guard.check = self._orig_mg_check
        kill_chain.reset_configuration()

    def _registry_with_probe(self):
        reg = tools_registry_mod.ToolRegistry()
        called = []
        reg.register(
            "a2_probe_write", "probe mutating tool",
            {"type": "object", "properties": {}},
            lambda **kw: called.append("write") or {"ok": True})
        return reg, called


class TestMoneyGuardUnavailable(_A2Base):
    def test_guard_raise_denies_confirm_ask_handler_never_called(self):
        called = []
        confirm_gate.register_handler(
            lambda *a, **k: called.append("asked") or True)
        money_guard.check = _raise()
        self.assertIs(
            confirm_gate.request_confirmation(
                "send_email",
                {"to": "friend@example.test",
                 "subject": "one-time code 123456",
                 "body": "your code is 123456"},
                timeout_s=5),
            False)
        self.assertEqual(called, [])

    def test_guard_raise_forces_gate_in_funnel_predicate(self):
        money_guard.check = _raise()
        verdict = tools_gate_mod._money_guard_refusal(
            "send_email", {"to": "friend@example.test"})
        self.assertEqual(verdict.state, GuardState.UNAVAILABLE)
        self.assertTrue(
            tools_gate_mod._needs_confirmation(
                "send_email", {"to": "friend@example.test"}))

    def test_guard_ok_still_allows_predicate(self):
        verdict = tools_gate_mod._money_guard_refusal(
            "send_email", {"to": "friend@example.test"})
        self.assertEqual(verdict.state, GuardState.ALLOWED)


class TestFailsafeUnavailable(_A2Base):
    def test_native_mutating_denied_read_allowed(self):
        reg, called = self._registry_with_probe()
        failsafe.tripped = _raise()
        result = reg.execute("a2_probe_write", {})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "refused")
        self.assertEqual(called, [])
        # Read-class tool: exempt exactly as when tripped.
        result = reg.execute("get_current_time_date", {})
        self.assertEqual(result.get("status"), "success")

    def test_tri_state_helper_reports_unavailable(self):
        failsafe.tripped = _raise()
        state, reason = tools_registry_mod._failsafe_state()
        self.assertIs(state, FailsafeState.UNAVAILABLE)
        self.assertEqual(reason, "RuntimeError")

    def test_mcp_nonread_denied_read_proceeds(self):
        import core.mcp_client as mcp_client

        class _FakeBus:
            def __init__(self):
                self.dispatched = []

            def resolve(self, name):
                if name == "mcp__srv__probe_write":
                    return {"server": "srv", "tool": "probe_write",
                            "annotations": {},
                            "parameters": {"type": "object",
                                           "properties": {}},
                            "description": "probe write"}
                if name == "mcp__srv__probe_read":
                    return {"server": "srv", "tool": "probe_read",
                            "annotations": {"readOnlyHint": True},
                            "parameters": {"type": "object",
                                           "properties": {}},
                            "description": "probe read"}
                return None

            def tool_changed(self, name):
                return False

            def ensure_server(self, server):
                return True, None

            def call_tool_sync(self, server, tool, args, timeout=120.0):
                self.dispatched.append((server, tool))
                return True, "fake mcp result", None

        bus = _FakeBus()
        orig_get_bus = mcp_client.get_bus
        mcp_client.get_bus = lambda: bus
        # Audit A3 (P0-5): read treatment now comes ONLY from
        # the local operator-reviewed policy, never from the
        # server's readOnlyHint. Review exactly this
        # declaration as read so the read-exemption leg below
        # still exercises a read-class tool; the unreviewed
        # case is pinned by tests/test_auditfixA3.py.
        import core.mcp_policy as mcp_policy

        _probe_read_entry = {
            "server": "srv", "tool": "probe_read",
            "parameters": {"type": "object", "properties": {}},
            "description": "probe read"}
        mcp_policy.set_store({
            mcp_policy.policy_key(_probe_read_entry): {
                "class": "read",
                "digest": mcp_policy.declaration_digest(
                    _probe_read_entry)}})
        try:
            failsafe.tripped = _raise()
            reg = tools_registry_mod.ToolRegistry()
            result = reg.execute("mcp__srv__probe_write", {})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result.get("status"), "refused")
            self.assertEqual(result.get("kind"), "refused")
            self.assertEqual(bus.dispatched, [])
            # Read-class MCP call stays exempt (read <=> non-egress).
            result = reg.execute("mcp__srv__probe_read", {})
            self.assertEqual(result.get("status"), "success")
            self.assertEqual(bus.dispatched, [("srv", "probe_read")])
        finally:
            mcp_client.get_bus = orig_get_bus
            mcp_policy.reset_store()


class TestKillChainUnavailable(_A2Base):
    def test_native_mutating_denied(self):
        reg, called = self._registry_with_probe()
        kill_chain.gate_tool = _raise()
        result = reg.execute("a2_probe_write", {})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "refused")
        self.assertEqual(called, [])

    def test_native_read_exemption_follows_gate_classification(self):
        # Egress batch: INTENTIONAL behavior change — the
        # old pin mirrored the inverted classification
        # (network get_weather proceeded, local
        # get_current_time_date was refused, under an
        # unconsultable gate). Corrected: a purely local
        # read proceeds under UNAVAILABLE (the documented
        # rung-5 residual), while a network read is
        # refused — exactly what rung 2 (freeze-egress)
        # does. The handler is stubbed so no network is
        # touched; kind/message assertions beside the
        # classification are unchanged.
        reg = tools_registry_mod.ToolRegistry()
        called = []
        reg.register("get_weather", "stubbed weather",
                     {"type": "object", "properties": {}},
                     lambda **kw: called.append("weather")
                     or {"ok": True})
        kill_chain.gate_tool = _raise()
        result = reg.execute("get_current_time_date", {})
        self.assertEqual(result.get("status"), "success")
        result = reg.execute("get_weather", {})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "refused")
        self.assertEqual(called, [])

    def test_corrupt_state_raises_never_reads_as_rung_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "kill_chain.json"
            bad.write_text("{not json", encoding="utf-8")
            kill_chain.configure(tmp)
            with self.assertRaises(kill_chain._KillChainUnavailable):
                kill_chain._strict_rung()
            with self.assertRaises(kill_chain._KillChainUnavailable):
                kill_chain.gate_tool("write_file", False, False)

    def test_missing_state_file_is_still_rung_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            kill_chain.configure(tmp)
            self.assertEqual(kill_chain._strict_rung(), 0)
            self.assertIsNone(
                kill_chain.gate_tool("write_file", False, False))


class TestBudgetUnavailable(_A2Base):
    def test_mutating_refused_read_proceeds_unrecorded(self):
        tools_handlers_mod._budget_now = _raise()
        msg = tools_handlers_mod._budget_check("a2_probe_write", False)
        self.assertIn("unavailable", msg)
        self.assertIn("not run", msg)
        before = list(tools_handlers_mod._budget_timestamps)
        self.assertIsNone(
            tools_handlers_mod._budget_check("get_current_time_date",
                                            True))
        self.assertEqual(
            list(tools_handlers_mod._budget_timestamps), before)

    def test_funnel_mutating_refused_read_succeeds(self):
        reg, called = self._registry_with_probe()
        tools_handlers_mod._budget_now = _raise()
        result = reg.execute("a2_probe_write", {})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "budget")
        self.assertEqual(called, [])
        result = reg.execute("get_current_time_date", {})
        self.assertEqual(result.get("status"), "success")


class TestExplicitlyDisabled(_A2Base):
    def test_budget_zero_permits_but_error_still_denies(self):
        # Declared policy (limit 0) disables the brake: proceed.
        tools_handlers_mod._tool_call_budget = lambda: 0
        self.assertIsNone(
            tools_handlers_mod._budget_check("a2_probe_write", False))
        self.assertIsNone(
            tools_handlers_mod._budget_check("get_current_time_date",
                                            True))
        # An error is NOT a disable: the money guard still denies.
        money_guard.check = _raise()
        verdict = tools_gate_mod._money_guard_refusal(
            "send_email", {"to": "friend@example.test"})
        self.assertEqual(verdict.state, GuardState.UNAVAILABLE)
        self.assertIs(
            confirm_gate.request_confirmation(
                "send_email", {"to": "friend@example.test"},
                timeout_s=5),
            False)


if __name__ == "__main__":
    unittest.main()
