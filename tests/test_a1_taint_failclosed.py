"""A1 taint-consult fail-closed pins (2026-11-05, parent wave).

The fail-open audit (FAILOPEN_AUDIT.md, item A1) found that four
taint-consult sites — plus, on this batch's full-tree grep, the curl
read lane — read an unconsultable taint state as untainted/absent,
against core/injection.py's own contract: "The taint check must fail
CLOSED for state-changing tools — if the gate machinery raises, the
tool does not run (the parent wave owns that policy change)."
This batch IS that parent wave. Each test below patches
core.injection.tainted to raise (the essentially-unreachable failure
mode: a pure-stdlib in-memory read) and proves the site now takes
its fail-closed treatment, plus controls proving consultable-tainted
and consultable-clean behaviour is unchanged.

INTENTIONAL: every test names the site + the contract. Sites
deliberately NOT converted here (see the batch report): the native
gate predicate and the funnel taint-gate stage consult taint
UNGUARDED, so a raise propagates out of execute() and the tool
never runs (already fail-closed); the allowlist shortcut already
asks on unknown taint; the audit snapshot already records
"unknown"; the rungoal scope grant is fail-closed by construction
(taint grants a scope, so unconsultable withholds it); the routine
provenance record has no product readers and no unknown-bool in its
schema, so converting it would fabricate provenance.
"""

import unittest
from unittest import mock

from core import confirm_gate
from core import injection
from core.curl_cffi_lane import fetch_read
from core.funnel import money_guard as mg
from core.funnel import rule_of_two
from core.funnel import tools_gate


def _raising_taint():
    """Patch core.injection.tainted to raise (unconsultable state)."""
    return mock.patch.object(
        injection, "tainted", side_effect=RuntimeError("unconsultable"))


def _clean_taint():
    return mock.patch.object(injection, "tainted", return_value=False)


def _tainted_session(source="web_content"):
    """Patch tainted True with a fixed source + sources list."""
    return mock.patch.multiple(
        injection,
        tainted=mock.Mock(return_value=True),
        taint_source=mock.Mock(return_value=source),
        taint_sources=mock.Mock(return_value=[source]))


class TestA1McpNeedsConfirmation(unittest.TestCase):
    # tools_gate.mcp_needs_confirmation: tainted (any kind) asks.
    # Helpers patched to isolate the taint branch: a "read" class
    # with the provenance lock off and a non-money name, so only
    # the taint branch can return True.
    def _patched_helpers(self):
        return (mock.patch.object(tools_gate, "mcp_tool_class",
                                  return_value="read"),
                mock.patch.object(tools_gate, "_mcp_provenance_lock_active",
                                  return_value=False))

    def test_unconsultable_asks(self):
        entry = {"name": "test_read_tool"}
        cls_patch, lock_patch = self._patched_helpers()
        with cls_patch, lock_patch, _raising_taint():
            self.assertTrue(
                tools_gate.mcp_needs_confirmation(entry, {}, False))

    def test_clean_is_free(self):
        entry = {"name": "test_read_tool"}
        cls_patch, lock_patch = self._patched_helpers()
        with cls_patch, lock_patch, _clean_taint():
            self.assertFalse(
                tools_gate.mcp_needs_confirmation(entry, {}, False))

    def test_tainted_asks(self):
        entry = {"name": "test_read_tool"}
        cls_patch, lock_patch = self._patched_helpers()
        with cls_patch, lock_patch, _tainted_session():
            self.assertTrue(
                tools_gate.mcp_needs_confirmation(entry, {}, False))


class TestA1RuleOfTwoLegs(unittest.TestCase):
    # rule_of_two legs: leg A = tainted leg; the forbidden union
    # {A, B, C} asks a human. Unconsultable must count leg A.

    def test_native_unconsultable_adds_leg_a(self):
        with _raising_taint():
            self.assertIn("A", rule_of_two.legs_for_native_call(
                "some_tool", {}))

    def test_native_clean_has_no_leg_a(self):
        with _clean_taint():
            self.assertNotIn("A", rule_of_two.legs_for_native_call(
                "some_tool", {}))

    def test_native_tainted_adds_leg_a(self):
        with _tainted_session():
            self.assertIn("A", rule_of_two.legs_for_native_call(
                "some_tool", {}))

    def test_mcp_unconsultable_adds_leg_a(self):
        with _raising_taint():
            self.assertIn("A", rule_of_two.legs_for_mcp_call("read"))

    def test_mcp_clean_has_no_legs(self):
        with _clean_taint():
            self.assertEqual(set(), rule_of_two.legs_for_mcp_call("read"))

    def test_mcp_tainted_adds_leg_a(self):
        with _tainted_session():
            self.assertIn("A", rule_of_two.legs_for_mcp_call("read"))


class TestA1MoneyGuard(unittest.TestCase):
    # money_guard.check Rule 3: injection-originated money moves
    # are REFUSED, never routed to confirmation. Unconsultable
    # must yield "tainted-session", hence a Rule 3 refusal.
    # _MONEY_ARGS passes every other half (resolved payee, exact
    # amount under the first-seen cap, stated currency), so only
    # the source half can refuse it.
    _MONEY_ARGS = {"action": "pay", "payee": "Alice", "amount": 100,
                   "currency": "INR"}

    def _assert_source_half_refusal(self, refusal):
        self.assertIsNotNone(refusal)
        self.assertEqual(mg.RULE_3, refusal.rule)
        self.assertIn("source is tool, web, or document content",
                      refusal.message)

    def test_unconsultable_refuses_money_move(self):
        with _raising_taint():
            refusal = mg.check("pay_money", dict(self._MONEY_ARGS))
        self._assert_source_half_refusal(refusal)

    def test_clean_allows_money_move(self):
        with _clean_taint():
            self.assertIsNone(
                mg.check("pay_money", dict(self._MONEY_ARGS)))

    def test_tainted_refuses_money_move(self):
        with _tainted_session():
            refusal = mg.check("pay_money", dict(self._MONEY_ARGS))
        self._assert_source_half_refusal(refusal)

    def test_explicit_tainted_param_untouched(self):
        # Caller-pinned taint bypasses the consult entirely.
        refusal = mg.check("pay_money", dict(self._MONEY_ARGS),
                           tainted=True)
        self._assert_source_half_refusal(refusal)
        self.assertIsNone(
            mg.check("pay_money", dict(self._MONEY_ARGS), tainted=False))


class TestA1ConfirmTaintContext(unittest.TestCase):
    # confirm_gate._taint_context is ADVISORY (warning text + never
    # batch). Unconsultable must warn + never batch, with the
    # source honestly "unknown" — never claimed as a real source.

    def test_unconsultable_warns_with_unknown_source(self):
        with _raising_taint():
            self.assertEqual((True, ("unknown", [])),
                             confirm_gate._taint_context())

    def test_clean_is_untainted(self):
        with _clean_taint():
            self.assertEqual((False, (None, [])),
                             confirm_gate._taint_context())

    def test_tainted_reports_source(self):
        with _tainted_session():
            self.assertEqual((True, ("web_content", ["web_content"])),
                             confirm_gate._taint_context())


class TestA1CurlLane(unittest.TestCase):
    # curl_cffi_lane.fetch_read: the lane has no popup, so a
    # tainted lane is REFUSED outright. Unconsultable must refuse,
    # not open the egress read.
    _KWARGS = {
        "url": "https://example.com/",
        "cookies": [{"name": "session", "value": "abc",
                     "domain": "example.com"}],
        "cookie_source": "playwright_context",
        "impersonate": "chrome",
    }

    def _fake_transport(self, url, cookies, impersonate, timeout):
        return 200, "ok"

    def test_unconsultable_refuses_lane(self):
        with _raising_taint():
            result = fetch_read(_transport=self._fake_transport,
                                **self._KWARGS)
        self.assertEqual("error", result["status"])
        self.assertEqual("refused", result["kind"])
        self.assertIn("tainted", result["message"])

    def test_clean_succeeds(self):
        with _clean_taint():
            result = fetch_read(_transport=self._fake_transport,
                                **self._KWARGS)
        self.assertEqual("success", result["status"])

    def test_tainted_refuses_lane(self):
        with _tainted_session():
            result = fetch_read(_transport=self._fake_transport,
                                **self._KWARGS)
        self.assertEqual("error", result["status"])
        self.assertEqual("refused", result["kind"])
        self.assertIn("tainted", result["message"])


if __name__ == "__main__":
    unittest.main()
