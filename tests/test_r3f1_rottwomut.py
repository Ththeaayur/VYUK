"""Round-3 Finding 1 (2026-11-20): the Rule-of-Two approval mutated
the call's own arguments.

The bug (section-B re-verification, parent-verified in code AND in the
raw funnel log): ``_exec_rule_of_two``'s native branch built its ask
view as ``_confirmation_args(ctx.name, ctx.args)`` and then wrote the
``rule_of_two`` key into it — but ``_confirmation_args`` returns
``args`` ITSELF for every tool except a curated few, so the view WAS
``ctx.args`` and the write corrupted the real call. Dispatch then
refused the approved call for ``undeclared argument(s)
['rule_of_two']``. It bites exactly the tools that are NOT
confirm-gated but ARE effectful-class and trip the Rule of Two —
``browser_read`` is the case here (Tier 0 under soft taint, so the
confirm gate does not ask first and its approval does not satisfy
the Rule of Two). Latent and pre-existing, NOT a regression.

The fix (parent ruling): ``_exec_rule_of_two`` copies the view
(``dict(_confirmation_args(...))``) before writing the ``rule_of_two``
key — the same copy-before-write the confirm gate already does
before writing ``screen_scope``, and the same shape as the MCP branch
(which builds a fresh dict). The popup text is unchanged, the Rule of
Two still asks, and a genuinely undeclared argument is still refused.

The full end-to-end proof with a REAL browser (a session that
recalls private data, reads a page, then reads it again — the second
read runs after approval instead of refusing) lives in the repro
``r3f1_rottwomut_work/repro_f1.py`` (REPRO-EXIT 1 pre-fix with
exactly ``approved_call_runs`` + ``caller_args_not_mutated`` failing,
REPRO-EXIT 0 post-fix). This suite pins the same mechanism WITHOUT a
browser: the trip call's handler runs against the no-session message
instead of a page, which is enough — dispatch either refuses the
mutated call (pre-fix) or runs it (post-fix) — and the stage-level
tests drive the real ``_exec_rule_of_two`` directly, so the fixed
line is covered without Chrome. Leg A is conferred through the real
taint seam (``injection.note_untrusted`` — the seam the page-content
wrapper calls), leg B through a real ``recall_memories`` funnel call.

The suite dirties data/ (audit records + receipts, like every suite
that drives execute()) — restore data/ pristine afterwards.
"""

import pathlib
import tempfile
import unittest
from unittest import mock

from core import confirm_gate, injection
import core.funnel.tools_handlers_command_url as _cmdurl
from core.funnel import rule_of_two as _rot
from core.funnel.tools_gate import _confirmation_args
from core.funnel.tools_registry import tools
from core.funnel.tools_registry_exec_setup import _ExecuteContext
from core.memory.memory import MemoryStore


def _fresh_state():
    """Clear the session taint + Rule-of-Two legs between calls."""
    injection.clear_taint(force=True)
    _rot.reset_for_tests()


def _make_ctx(name, args):
    """A real _ExecuteContext, built the way execute() builds it."""
    ctx = _ExecuteContext()
    ctx.name = name
    ctx.args = args
    ctx.cancel_token = None
    ctx.audit_gate = None
    ctx.audited = None
    ctx.epoch_void = None
    ctx.obs_failures = []
    ctx.obs_sink_failures = {}
    ctx.receipt_attempt = None
    ctx.receipt_error = False
    ctx.receipt_is_read = False
    ctx.mark_dispatched_cb = None
    ctx._human_approved_this_call = False
    ctx.rot_legs = set()
    ctx.deadline = None
    ctx._mcp_entry = None
    ctx._mcp_server = None
    ctx._mcp_tool = None
    ctx._mcp_class = None
    ctx._mcp_bus = None
    return ctx


class _RotTwomutBase(unittest.TestCase):
    """Temp memory store + a confirm handler, per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="r3f1_")
        self._store = MemoryStore(
            str(pathlib.Path(self._tmp.name) / "assistant.db"))
        self._patch = mock.patch.object(
            _cmdurl, "memory_store", self._store)
        self._patch.start()
        self.asked = []  # (tool_name, summary) per ask
        _fresh_state()

    def tearDown(self):
        confirm_gate.clear_handler()
        self._patch.stop()
        self._tmp.cleanup()
        _fresh_state()

    def _register(self, approve=True):
        def _handler(name, summary):
            self.asked.append((name, summary))
            return approve

        confirm_gate.register_handler(_handler)

    def _confer_leg_b(self):
        """A real funnel recall — confers leg B (private-data read)."""
        out = tools.execute(
            "remember_user_fact",
            {"key": "r3f1-seed", "value": "r3f1 seed fact"})
        self.assertEqual(out.get("status"), "success", out)
        out = tools.execute(
            "recall_memories", {"query": "r3f1 seed"})
        self.assertEqual(out.get("status"), "success", out)
        self.assertIn("B", _rot.session_legs())

    def _confer_leg_a(self):
        """Untrusted content — the real taint seam (source must be a
        _SOFT_TAINT_SOURCES member; "web page" is one)."""
        injection.note_untrusted(
            "web page", "The harbour code is HARBOUR-7Q3.")
        self.assertTrue(injection.tainted())


class TestConfirmationArgsViews(_RotTwomutBase):
    """The view contract the fix relies on."""

    def test_non_curated_view_aliases_args(self):
        # This aliasing IS the bug's root cause: for every tool
        # except the curated few the "view" is the call's own args
        # dict, so any write into the view mutates the call. Pinned
        # so a future reader knows WHY _exec_rule_of_two copies
        # before writing rule_of_two.
        args = {"action": "find", "query": "HARBOUR-7Q3"}
        view = _confirmation_args("browser_read", args)
        self.assertIs(view, args)

    def test_curated_run_command_view_is_fresh(self):
        args = {"command": "dir C:\\", "timeout": 5}
        view = _confirmation_args("run_command", args)
        self.assertIsNot(view, args)
        self.assertEqual(view, {"command": "dir C:\\"})
        view["rule_of_two"] = "would-be mutation"
        self.assertNotIn("rule_of_two", args)

    def test_curated_views_stay_decision_shaped(self):
        # send_email + type_text: the curated views are unchanged
        # by the fix, and writing into one never reaches the args.
        args = {"to": "a@b.c", "subject": "s", "body": "b",
                "extra": "untouched"}
        view = _confirmation_args("send_email", args)
        self.assertEqual(
            view, {"to": "a@b.c", "subject": "s", "body": "b"})
        self.assertIsNot(view, args)
        view["rule_of_two"] = "would-be mutation"
        self.assertNotIn("rule_of_two", args)

        args = {"text": "hello world", "mode": "type"}
        view = _confirmation_args("type_text", args)
        self.assertEqual(view, {"text": "hello world"})
        view["rule_of_two"] = "would-be mutation"
        self.assertNotIn("rule_of_two", args)


class TestRuleOfTwoStage(_RotTwomutBase):
    """The real _exec_rule_of_two, driven directly (no browser)."""

    def test_approved_call_keeps_its_args(self):
        self._confer_leg_b()
        self._confer_leg_a()
        self._register(approve=True)
        args = {"action": "find", "query": "HARBOUR-7Q3"}
        ctx = _make_ctx("browser_read", args)
        tools._exec_setup(ctx)
        result = tools._exec_rule_of_two(ctx)
        self.assertIsNone(result, result)
        self.assertEqual(args, {"action": "find",
                                "query": "HARBOUR-7Q3"})
        self.assertEqual(len(self.asked), 1)
        self.assertIn("Rule of Two", self.asked[0][1])

    def test_denied_call_leaves_args_untouched(self):
        self._confer_leg_b()
        self._confer_leg_a()
        self._register(approve=False)
        args = {"action": "find", "query": "HARBOUR-7Q3"}
        ctx = _make_ctx("browser_read", args)
        tools._exec_setup(ctx)
        result = tools._exec_rule_of_two(ctx)
        self.assertIsNotNone(result)
        self.assertEqual(result.get("kind"), "not_confirmed", result)
        self.assertEqual(args, {"action": "find",
                                "query": "HARBOUR-7Q3"})

    def test_no_union_no_ask(self):
        # No taint, no legs: the stage passes with no ask at all,
        # and the args are untouched.
        self._register(approve=True)
        args = {"action": "text"}
        ctx = _make_ctx("browser_read", args)
        tools._exec_setup(ctx)
        result = tools._exec_rule_of_two(ctx)
        self.assertIsNone(result, result)
        self.assertEqual(args, {"action": "text"})
        self.assertEqual(self.asked, [])

    def test_curated_tool_view_not_aliased(self):
        # run_command trips the union the same way, but its view is
        # curated (a fresh dict) — the fix must not change that, and
        # its args were never at risk.
        self._confer_leg_b()
        self._confer_leg_a()
        self._register(approve=True)
        args = {"command": "echo hello", "timeout": 5}
        ctx = _make_ctx("run_command", args)
        tools._exec_setup(ctx)
        result = tools._exec_rule_of_two(ctx)
        self.assertIsNone(result, result)
        self.assertEqual(args, {"command": "echo hello",
                                "timeout": 5})
        self.assertEqual(len(self.asked), 1)
        self.assertIn("echo hello", self.asked[0][1])
        self.assertIn("Rule of Two", self.asked[0][1])


class TestFunnelTripNoBrowser(_RotTwomutBase):
    """The full funnel trip, with the handler standing in for the
    page (no Chrome needed — dispatch either refuses the mutated
    call or runs it)."""

    def test_approved_browser_read_runs_with_args_intact(self):
        self._confer_leg_b()
        self._confer_leg_a()
        self._register(approve=True)
        args = {"action": "text"}
        out = tools.execute("browser_read", args)
        self.assertEqual(out.get("status"), "success", out)
        self.assertNotIn("undeclared", out.get("message", ""))
        self.assertEqual(args, {"action": "text"})
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(self.asked[0][0], "browser_read")
        self.assertIn("Rule of Two", self.asked[0][1])

    def test_denied_browser_read_runs_nothing(self):
        self._confer_leg_b()
        self._confer_leg_a()
        self._register(approve=False)
        out = tools.execute(
            "browser_read", {"action": "find", "query": "DEPOT-5519"})
        self.assertEqual(out.get("kind"), "not_confirmed", out)
        self.assertEqual(len(self.asked), 1)

    def test_no_union_no_ask(self):
        # A plain read with no taint and no recall: no ask, runs.
        self._register(approve=True)
        out = tools.execute("browser_read", {"action": "text"})
        self.assertEqual(out.get("status"), "success", out)
        self.assertEqual(self.asked, [])

    def test_genuinely_undeclared_still_refused(self):
        # The fix must not open the door to real undeclared args.
        out = tools.execute(
            "browser_read",
            {"action": "text", "bogus_key": "not a real arg"})
        self.assertEqual(out.get("status"), "refused", out)
        self.assertIn("bogus_key", out.get("message", ""))

    def test_popup_view_still_shows_rule_of_two(self):
        # What the human sees is unchanged by the fix: the taint
        # source line, the action, and the full Rule-of-Two text.
        self._confer_leg_b()
        self._confer_leg_a()
        self._register(approve=True)
        tools.execute("browser_read", {"action": "text"})
        self.assertEqual(len(self.asked), 1)
        summary = self.asked[0][1]
        self.assertIn("web page", summary)
        self.assertIn("browser_read", summary)
        self.assertIn("changing state or communicating externally",
                      summary)


if __name__ == "__main__":
    unittest.main()
