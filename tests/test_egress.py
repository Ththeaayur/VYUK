"""Egress-classification batch — the rung-2 inversion fix.

Carried anomaly from Build A2 (which deliberately preserved
it, mirroring valid-state verdicts byte-identically, because
fixing it alters rung-2 behavior for every tool): at kill-chain
rung 2 (freeze-egress — "Network is paused, nothing leaves
this computer"), the funnel refused the purely local
``get_current_time_date`` as gate-egress-class while allowing
the real network read ``get_weather``.

Root cause, documented here so the polarity is never
re-derived wrong again: ``core.funnel.tools_gate._is_egress_read``
already means "this read can put model-chosen bytes on the
wire" — False for provably-local reads (``_LOCAL_ONLY_READS``),
True for the explicit egress set AND for any other read-only
tool not proven local. The native funnel passed
``is_egress_class=not _is_egress_read(name)`` to
``kill_chain.gate_tool`` (and the UNAVAILABLE allowance used
the same inverted test), so gate_tool's "network is frozen"
branch fired for local reads and never for network reads.
Every other consumer of the predicate (taint gate, pclass
Tier 2, rule_of_two's pure-read leg, the taint-gated label)
already used it unnegated — the kill-chain sites were the
only negation, which is how the inversion was identified.

INTENDED rung-2 semantics (from kill_chain.py's own rung
text): rung 2 freezes the NETWORK boundary. A call is
egress-class iff it can send bytes to, or fetch from, a
remote host — a fetch counts, because the query itself
leaves the machine. A purely local read (clock, local
files/tables, local computation) is NOT egress-class.
Writes are rung 1's business (freeze-tools) and are
unchanged by this batch.

HARD INVARIANT pinned here: at any rung, a purely local
read is NEVER more restricted than a network read. At
rung 2 the local read proceeds and the network read is
refused; at rung 1 both proceed (both are reads); at
rung 5 both are refused; under an unconsultable gate the
local read proceeds (the A2 rung-5 residual, preserved
in structure — only its covered set is corrected from
network reads to local reads) and the network read is
refused.

Edge cases, decided explicitly:
* Cached vs live: classification is by CAPABILITY, not by
  this call's luck. A tool that may go live on a cache miss
  is network-class; only tools the v5.46 audit proved
  local sit in _LOCAL_ONLY_READS. This batch fixes the
  POLARITY only — no tool moves between the sets.
* Reads that send a query outward (search, weather, DNS)
  are egress: the query leaves the machine.
* Reads with side effects classified as reads today keep
  their read/write classification; this batch changes
  only the egress axis. A read-class tool that fetches
  is egress; a non-read tool is refused at rung 1 first,
  so rung 2's branch never sees it in practice.
* Plugin gate classes and the MCP path are unchanged:
  plugin egress still maps via _is_egress_read, and MCP
  tools map via the operator-reviewed policy class
  (read -> allowed at rung 2, egress/confirm -> refused),
  which was already the correct polarity. A policy-"read"
  MCP tool that in fact fetches is an operator
  misclassification, not a code inversion, and is out of
  this batch's scope.
* The rung-5-behind-unreadable-consult residual is
  EXPLICITLY LEFT OPEN (separate follow-up decision):
  under UNAVAILABLE, pure local reads still proceed.

Mocks / fault injection only: network handlers are
stubbed, so no network is touched; the only real handler
executed is the local clock read.
"""

import json
import tempfile
import unittest
from pathlib import Path

from core import confirm_gate  # Phase 3 F2b INTENTIONAL: kill_chain split to its own funnel import below
from core.funnel import kill_chain  # Phase 3 F2b INTENTIONAL: kill_chain moved to core/funnel/
from core.funnel import tools_gate as tools_gate_mod  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel import tools_handlers as tools_handlers_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel import tools_registry as tools_registry_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/


def _raise(exc_type=RuntimeError):
    def _f(*args, **kwargs):
        raise exc_type("injected consult failure")

    return _f


_LOCAL_SAMPLE = [
    "get_current_time_date",
    "read_file",
    "list_installed_apps",
    "battery_info",
]

_NETWORK_SAMPLE = [
    "get_weather",   # inferred network (not in _LOCAL_ONLY_READS)
    "web_search",    # inferred network
    "dns_lookup",    # explicit _EGRESS_TOOLS member
    "http_headers",  # explicit _EGRESS_TOOLS member
    "ping_host",     # explicit _EGRESS_TOOLS member
]


class _EgressBase(unittest.TestCase):
    def setUp(self):
        self._orig_log = confirm_gate._log_decision
        confirm_gate._log_decision = lambda *a, **k: None
        confirm_gate.clear_handler()
        self._orig_budget_now = tools_handlers_mod._budget_now
        self._orig_budget_fn = tools_handlers_mod._tool_call_budget
        self._orig_timestamps = list(tools_handlers_mod._budget_timestamps)
        self._orig_gate_tool = kill_chain.gate_tool
        self._tmp = tempfile.TemporaryDirectory()
        kill_chain.configure(self._tmp.name)

    def tearDown(self):
        confirm_gate._log_decision = self._orig_log
        confirm_gate.clear_handler()
        tools_handlers_mod._budget_now = self._orig_budget_now
        tools_handlers_mod._tool_call_budget = self._orig_budget_fn
        tools_handlers_mod._budget_timestamps[:] = self._orig_timestamps
        kill_chain.gate_tool = self._orig_gate_tool
        kill_chain.reset_configuration()
        self._tmp.cleanup()

    def _set_rung(self, rung):
        path = Path(self._tmp.name) / "kill_chain.json"
        path.write_text(json.dumps({"rung": rung}), encoding="utf-8")

    def _registry_with_network_stubs(self, called):
        reg = tools_registry_mod.ToolRegistry()

        def _stub(name):
            def _handler(**kw):
                called.append(name)
                return {"ok": True}
            return _handler

        for name in ("get_weather", "web_search", "dns_lookup"):
            reg.register(
                name, "stubbed network read",
                {"type": "object", "properties": {}},
                _stub(name))
        return reg


class TestPredicatePolarity(_EgressBase):
    def test_local_read_is_not_egress_network_read_is(self):
        # The predicate itself is UNCHANGED by this batch —
        # only its use at the kill-chain sites was inverted.
        # Both headline tools are read-class; the ONLY
        # difference between them is the egress axis, which
        # is exactly what the old code got backwards.
        self.assertTrue(
            tools_gate_mod._is_read_class("get_current_time_date", {}))
        self.assertTrue(
            tools_gate_mod._is_read_class("get_weather", {}))
        self.assertIs(
            tools_gate_mod._is_egress_read("get_current_time_date"), False)
        self.assertIs(
            tools_gate_mod._is_egress_read("get_weather"), True)

    def test_representative_sets_have_expected_polarity(self):
        for name in _LOCAL_SAMPLE:
            self.assertIs(
                tools_gate_mod._is_egress_read(name), False, name)
        for name in _NETWORK_SAMPLE:
            self.assertIs(
                tools_gate_mod._is_egress_read(name), True, name)


class TestGateToolDirect(_EgressBase):
    def test_rung_two_refuses_network_allows_local(self):
        self._set_rung(2)
        for name in _LOCAL_SAMPLE:
            self.assertIsNone(
                kill_chain.gate_tool(
                    name, True, tools_gate_mod._is_egress_read(name)),
                name)
        for name in _NETWORK_SAMPLE:
            verdict = kill_chain.gate_tool(
                name, True, tools_gate_mod._is_egress_read(name))
            self.assertIn("network is frozen", verdict, name)

    def test_write_refusal_is_tools_message_not_network(self):
        # Non-read treatment is unchanged: the tools branch
        # fires first at rung 2, so a write is refused as a
        # frozen tool, never as a network call.
        self._set_rung(2)
        verdict = kill_chain.gate_tool("write_file", False, False)
        self.assertIn("tools are frozen", verdict)
        self.assertNotIn("network is frozen", verdict)


class TestFunnelRungTwo(_EgressBase):
    def test_local_time_proceeds_network_refused(self):
        self._set_rung(2)
        called = []
        reg = self._registry_with_network_stubs(called)
        result = reg.execute("get_current_time_date", {})
        self.assertEqual(result.get("status"), "success")
        for name in ("get_weather", "web_search", "dns_lookup"):
            result = reg.execute(name, {})
            self.assertEqual(result.get("status"), "refused", name)
            self.assertEqual(result.get("kind"), "refused", name)
            self.assertIn("network is frozen",
                          result.get("message", ""), name)
        self.assertEqual(called, [])

    def test_write_still_refused_as_tools_not_network(self):
        self._set_rung(2)
        reg = tools_registry_mod.ToolRegistry()
        called = []
        reg.register(
            "egress_probe_write", "probe mutating tool",
            {"type": "object", "properties": {}},
            lambda **kw: called.append("write") or {"ok": True})
        result = reg.execute("egress_probe_write", {})
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "refused")
        self.assertIn("tools are frozen", result.get("message", ""))
        self.assertNotIn("network is frozen", result.get("message", ""))
        self.assertEqual(called, [])


class TestFunnelOtherRungs(_EgressBase):
    def test_rung_one_and_zero_both_reads_proceed(self):
        # Rung 1 gates writes only; rung 0 gates nothing.
        # The invariant's lower legs: no rung below 2
        # distinguishes local from network reads.
        for rung in (0, 1):
            self._set_rung(rung)
            called = []
            reg = self._registry_with_network_stubs(called)
            result = reg.execute("get_current_time_date", {})
            self.assertEqual(result.get("status"), "success")
            result = reg.execute("get_weather", {})
            self.assertEqual(result.get("status"), "success")
            self.assertEqual(called, ["get_weather"])

    def test_rung_five_refuses_both(self):
        self._set_rung(5)
        called = []
        reg = self._registry_with_network_stubs(called)
        result = reg.execute("get_current_time_date", {})
        self.assertEqual(result.get("status"), "refused")
        self.assertIn("fully stopped", result.get("message", ""))
        result = reg.execute("get_weather", {})
        self.assertEqual(result.get("status"), "refused")
        self.assertIn("fully stopped", result.get("message", ""))
        self.assertEqual(called, [])


class TestFunnelUnavailable(_EgressBase):
    def test_local_proceeds_network_refused(self):
        # Corrected UNAVAILABLE rule: only a read-class,
        # provably-local call proceeds. The rung-5 residual
        # itself is preserved (a local read still runs
        # under an unconsultable gate); what changed is
        # the covered set — local reads, not network ones.
        kill_chain.gate_tool = _raise()
        called = []
        reg = self._registry_with_network_stubs(called)
        result = reg.execute("get_current_time_date", {})
        self.assertEqual(result.get("status"), "success")
        result = reg.execute("get_weather", {})
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "refused")
        self.assertIn("could not be consulted",
                      result.get("message", ""))
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
