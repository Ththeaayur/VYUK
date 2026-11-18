"""Router-consistency batch (wiring audit WIR-03 + WIR-11).

WIR-03 — the contract: the registry's empty-object rewrite
(tools_registry._gemini_safe_schema) says a nested object with
no listed fields becomes a JSON-text string, yet the synthetic
domain-router declarations carry exactly that shape as their
args node and never pass through the rewrite. The resolution
is a scope fix, not a shape change: the rewrite applies to
REGISTERED declarations only; router args is a documented
pass-through whose schema is the inner tool's schema, enforced
at dispatch. These tests pin both sides of that boundary:
  * the rewrite still fires for registered declarations
    (bare nested objects become JSON-text strings);
  * built router args nodes keep the documented pass-through
    shape (object, description, no properties);
  * routed calls still execute end-to-end through the funnel
    (success, confirm gate, validation, budget discipline);
  * genuinely malformed router args are refused, never
    silently accepted.
WIR-11 — the prompt: ROUTER_PROMPT used to describe a
use_toolbox-only world while route_* routers are live. The
rewrite names the real surface, and these tests pin it.

Budget discipline (test-isolation guest rule): funnel executes
use the process-global budget, so every test snapshots budget
timestamps/clock plus the confirm handler in setUp and restores
them byte-for-byte in tearDown. The full run's saturated
profile must be untouched by this suite.
"""

import unittest

from core import confirm_gate
from core.brain import domain_routers as routers_mod  # Phase 3 B1b INTENTIONAL: domain_routers moved to core/brain/
from core.funnel import tools_handlers as tools_handlers_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel.toolbox import ROUTER_PROMPT  # Phase 3 F2a INTENTIONAL: toolbox moved to core/funnel/
from core.funnel.toolbox import gemini_decl_for_names  # Phase 3 F2a INTENTIONAL: toolbox moved to core/funnel/
from core.funnel.tools_registry import ToolRegistry  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel.tools_registry import tools as real_tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/


class _RouterBase(unittest.TestCase):
    def setUp(self):
        self._saved_handler = getattr(confirm_gate, "_handler", None)
        self._had_handler = confirm_gate.has_handler()
        confirm_gate.clear_handler()
        self._orig_budget_now = tools_handlers_mod._budget_now
        self._orig_timestamps = list(tools_handlers_mod._budget_timestamps)
        # Test-isolation guest rule: snapshot above, then give THIS
        # test a clean budget window (the full run's window is
        # saturated by the time this suite runs, and a bare
        # snapshot would leave our funnel executes refused with
        # kind 'budget'). tearDown restores the snapshot
        # byte-for-byte, so later suites see exactly what they
        # would have seen without this suite.
        tools_handlers_mod.reset_tool_budget()

    def tearDown(self):
        confirm_gate.register_handler(self._saved_handler)
        assert confirm_gate.has_handler() == self._had_handler
        tools_handlers_mod._budget_now = self._orig_budget_now
        tools_handlers_mod._budget_timestamps[:] = self._orig_timestamps

    def _fresh_registry(self):
        return ToolRegistry()

    def _router_for(self, reg, tool):
        membership = routers_mod.domain_router_membership(reg)
        for rname, members in membership.items():
            if tool in members:
                return rname
        raise AssertionError(f"{tool} in no router: {membership!r}")


class TestSanitizerScope(_RouterBase):
    def test_rewrite_still_fires_for_bare_nested_object(self):
        # WIR-03, rule preserved: a nested object with no listed
        # fields becomes a JSON-text string.
        schema = {"type": "object", "properties": {
            "inner": {"type": "object",
                      "description": "A nested bundle."}}}
        out = ToolRegistry._gemini_safe_schema(schema)
        inner = out["properties"]["inner"]
        self.assertEqual(inner.get("type"), "string")
        self.assertIn("Give this as a JSON object written as text.",
                      inner.get("description", ""))

    def test_rewrite_applies_to_registered_declaration(self):
        # The exemption does not leak: a REGISTERED tool with a
        # bare nested object still gets rewritten by the shared
        # per-tool builder (gemini_decl_for_names), which carries
        # the registry's Gemini-safe rewrite (DECL-01: the legacy
        # all-direct builder is retired; this is its replacement
        # path for registered declarations).
        reg = self._fresh_registry()
        reg.register("router_probe_nested", "probe tool",
                     {"type": "object", "properties": {
                         "bundle": {"type": "object",
                                    "description": "A bundle."}}},
                     lambda **kw: {"ok": True})
        decls = gemini_decl_for_names(reg, ["router_probe_nested"])
        self.assertEqual(len(decls), 1)
        probe = decls[0]
        bundle = probe["parameters"]["properties"]["bundle"]
        self.assertEqual(bundle.get("type"), "string")
        self.assertIn("Give this as a JSON object written as text.",
                      bundle.get("description", ""))

    def test_router_args_keep_passthrough_shape(self):
        # Built router args nodes are exactly the documented
        # pass-through shape: object + description, no
        # properties — deliberately NOT rewritten to a string.
        routers = routers_mod.build_domain_routers(real_tools)
        self.assertEqual(len(routers), 11)
        for decl in routers:
            args_node = decl["parameters"]["properties"]["args"]
            self.assertEqual(args_node.get("type"), "object",
                             decl["name"])
            self.assertNotIn("properties", args_node, decl["name"])
            self.assertIn("Arguments for the named tool.",
                          args_node.get("description", ""),
                          decl["name"])
            # The router still declares tool + args, required tool.
            self.assertIn("tool", decl["parameters"]["required"])


class TestRoutedFunnel(_RouterBase):
    def _probe_registry(self):
        reg = self._fresh_registry()
        called = []
        reg.register(
            "router_probe_read", "probe read tool",
            {"type": "object",
             "properties": {"level": {"type": "number"}},
             "required": ["level"]},
            lambda **kw: called.append(kw) or {"ok": True, "echo": kw})
        reg.register(
            "run_command", "probe gated tool (name-gated)",
            {"type": "object",
             "properties": {"command": {"type": "string"}},
             "required": ["command"]},
            lambda **kw: called.append(kw) or {"ok": True})
        return reg, called

    def test_routed_read_executes_through_funnel(self):
        reg, called = self._probe_registry()
        router = self._router_for(reg, "router_probe_read")
        out = routers_mod.dispatch_route(
            reg, router, "router_probe_read", {"level": 0.5})
        self.assertEqual(out.get("status"), "success", out)
        self.assertEqual(len(called), 1)
        self.assertEqual(called[0].get("level"), 0.5)

    def test_routed_gated_tool_fails_closed_then_runs_approved(self):
        reg, called = self._probe_registry()
        router = self._router_for(reg, "run_command")
        # No handler: fail closed, stub never runs.
        out = routers_mod.dispatch_route(
            reg, router, "run_command", {"command": "echo hi"})
        self.assertEqual(out.get("status"), "refused", out)
        self.assertEqual(out.get("kind"), "not_confirmed", out)
        self.assertEqual(called, [])
        # Approving handler: the same routed call runs.
        confirm_gate.register_handler(lambda name, summary: True)
        try:
            out = routers_mod.dispatch_route(
                reg, router, "run_command", {"command": "echo hi"})
        finally:
            confirm_gate.clear_handler()
        self.assertEqual(out.get("status"), "success", out)
        self.assertEqual(len(called), 1)

    def test_routed_invalid_inner_args_refused_and_handler_skipped(self):
        # Inner schema is authoritative: a wrong-typed required
        # field is a validation refusal, and the handler never runs.
        reg, called = self._probe_registry()
        router = self._router_for(reg, "router_probe_read")
        out = routers_mod.dispatch_route(
            reg, router, "router_probe_read", {"level": "high"})
        self.assertEqual(out.get("status"), "refused", out)
        self.assertEqual(out.get("kind"), "validation", out)
        self.assertEqual(called, [])

    def test_routed_malformed_args_rejected_never_silently_accepted(self):
        reg, called = self._probe_registry()
        router = self._router_for(reg, "router_probe_read")
        for bad in ("{not json", ["x"], 42, None):
            out = routers_mod.dispatch_route(
                reg, router, "router_probe_read", bad)
            self.assertEqual(out.get("status"), "refused", out)
            self.assertEqual(out.get("kind"), "validation", out)
        # Omitted args ({}) still validate: the required field
        # is missing, so this is a validation refusal, not a
        # silent no-argument call.
        out = routers_mod.dispatch_route(
            reg, router, "router_probe_read", {})
        self.assertEqual(out.get("status"), "refused", out)
        self.assertEqual(out.get("kind"), "validation", out)
        self.assertEqual(called, [])

    def test_routed_unknown_tool_and_router_refuse(self):
        reg, called = self._probe_registry()
        router = self._router_for(reg, "router_probe_read")
        unknown = routers_mod.dispatch_route(
            reg, router, "no_such_tool_xyz", {})
        self.assertEqual(unknown.get("status"), "failure")
        self.assertEqual(unknown.get("kind"), "unavailable")
        no_router = routers_mod.dispatch_route(
            reg, "route_nope", "router_probe_read", {"level": 1})
        self.assertEqual(no_router.get("status"), "failure")
        self.assertEqual(no_router.get("kind"), "unavailable")
        self.assertEqual(called, [])


class TestRouterPrompt(_RouterBase):
    def test_prompt_names_routers_not_toolbox_only(self):
        # WIR-11: the prompt must name the live route_* surface
        # and must not describe a use_toolbox-only world.
        self.assertIn("route_<domain>", ROUTER_PROMPT)
        self.assertIn("domain router", ROUTER_PROMPT)
        self.assertNotIn("Every other tool is reached through use_toolbox",
                         ROUTER_PROMPT)
        # use_toolbox is still described — for discovery.
        self.assertIn("use_toolbox", ROUTER_PROMPT)
        self.assertIn("action 'find'", ROUTER_PROMPT)
        self.assertIn("action 'describe'", ROUTER_PROMPT)
        self.assertIn("action 'call'", ROUTER_PROMPT)
        # The v5.50 round-economy shortcuts survive the rewrite.
        self.assertIn("confident match", ROUTER_PROMPT)
        self.assertIn("retry once", ROUTER_PROMPT)
        # A refusal on either path is final.
        self.assertIn("refusal", ROUTER_PROMPT)
        self.assertIn("never retry around it", ROUTER_PROMPT)


if __name__ == "__main__":
    unittest.main()
