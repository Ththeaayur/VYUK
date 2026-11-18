"""Audit fix A1 — confirmation integrity (master audit P0-1, P1-6, P1-7).

Mocks only: no real email is ever sent, no real tool handler runs.
The "tool" in the decline test is a list append standing in for the
funnel's contract (a declined confirmation must never reach the tool).
"""

import logging
import threading
import unittest

from core import confirm_gate


def _tainted():
    return True, ("file", ["file"])


def _untainted():
    return False, (None, [])


def _email_args(suffix):
    # Subject/body differ ONLY after char 30 (the old summary clip):
    # the old display summaries of the two payloads are identical.
    return {
        "to": "friend@example.test",
        "subject": "x" * 40 + suffix,
        "body": "y" * 40 + suffix,
    }


class _A1Base(unittest.TestCase):
    def setUp(self):
        self._orig_log = confirm_gate._log_decision
        confirm_gate._log_decision = lambda *a, **k: None
        self._orig_taint = confirm_gate._taint_context
        confirm_gate._taint_context = _tainted
        self._reset_batch()
        confirm_gate.clear_handler()

    def tearDown(self):
        confirm_gate._taint_context = self._orig_taint
        confirm_gate._log_decision = self._orig_log
        self._reset_batch()
        confirm_gate.clear_handler()

    def _reset_batch(self):
        with confirm_gate._batch_lock:
            confirm_gate._batch_last.update(
                {"key": None, "decision": False, "at": 0.0,
                 "count": 0, "origin_id": None})

    def _ask(self, tool, args, handler, timeout_s=10):
        confirm_gate.register_handler(handler)
        return confirm_gate.request_confirmation(
            tool, args, timeout_s=timeout_s)


class TestP01ApprovalReplay(_A1Base):
    def test_differing_emails_prompt_separately(self):
        """P0-1: two emails differing only after the old clip point
        must prompt separately (old summaries are identical, so the
        old summary key would have batched them)."""
        alpha = _email_args("ALPHA")
        beta = _email_args("BETA")
        self.assertEqual(confirm_gate.summarize_args(alpha),
                         confirm_gate.summarize_args(beta))
        calls = []

        def handler(name, summary):
            calls.append(name)
            return True

        self.assertTrue(self._ask("send_email", alpha, handler))
        self.assertTrue(self._ask("send_email", beta, handler))
        self.assertEqual(calls, ["send_email", "send_email"])

    def test_declined_second_email_never_reaches_tool(self):
        """P0-1 acceptance: declining the second of two differing
        emails guarantees its tool handler never runs."""
        decisions = iter([True, False])
        tool_calls = []

        def handler(name, summary):
            return next(decisions)

        if self._ask("send_email", _email_args("ALPHA"), handler):
            tool_calls.append("ALPHA")
        if self._ask("send_email", _email_args("BETA"), handler):
            tool_calls.append("BETA")
        self.assertEqual(tool_calls, ["ALPHA"])

    def test_identical_nonsensitive_repeat_batches(self):
        """The digest key still batches TRUE identicals (and
        reordered-key identicals) for non-sensitive tools."""
        calls = []

        def handler(name, summary):
            calls.append(name)
            return True

        args = {"note": "hello", "count": 3}
        reordered = {"count": 3, "note": "hello"}
        self.assertTrue(self._ask("test_note_tool", args, handler))
        self.assertTrue(self._ask("test_note_tool", reordered, handler))
        self.assertEqual(len(calls), 1)
        self.assertEqual(confirm_gate.last_batch_count(), 2)

    def test_sensitive_identical_repeat_never_batches(self):
        """Even a byte-identical sensitive repeat prompts afresh."""
        calls = []

        def handler(name, summary):
            calls.append(name)
            return True

        args = _email_args("SAME")
        self.assertTrue(self._ask("send_email", args, handler))
        self.assertTrue(self._ask("send_email", dict(args), handler))
        self.assertEqual(calls, ["send_email", "send_email"])

    def test_digest_and_exemption_units(self):
        self.assertEqual(
            confirm_gate._canonical_args_digest({"b": 1, "a": 2}),
            confirm_gate._canonical_args_digest({"a": 2, "b": 1}))
        self.assertNotEqual(
            confirm_gate._canonical_args_digest(_email_args("ALPHA")),
            confirm_gate._canonical_args_digest(_email_args("BETA")))
        self.assertTrue(confirm_gate._is_batch_exempt_tool(
            "send_email", _email_args("X")))
        self.assertTrue(confirm_gate._is_batch_exempt_tool(
            "run_command", {"command": "ls"}))
        self.assertTrue(confirm_gate._is_batch_exempt_tool(
            "pay", {"amount": 5}))
        self.assertFalse(confirm_gate._is_batch_exempt_tool(
            "test_note_tool", {"note": "hi"}))


class TestP16StrictBoolean(_A1Base):
    def test_handler_nonbool_responses_deny(self):
        """P1-6 handler path: 'no', 'false', None and 1 all deny
        and are logged as invalid responses; literal True approves."""
        confirm_gate._taint_context = _untainted
        for value in ("no", "false", None, 1, 0):
            with self.assertLogs("nova.confirm_gate",
                                 level="INFO") as logs:
                result = self._ask(
                    "test_note_tool", {"x": 1},
                    lambda n, s, v=value: v)
            self.assertFalse(result, f"{value!r} must deny")
            self.assertTrue(
                any("invalid confirmation response" in line
                    for line in logs.output),
                f"{value!r} must be logged as invalid")
        self.assertTrue(self._ask(
            "test_note_tool", {"x": 1}, lambda n, s: True))

    def test_resolve_pending_nonbool_denies(self):
        """P1-6 resolve path: a non-bool resolve answer resolves
        the request as a DENIAL, logged as invalid; True approves."""
        confirm_gate._taint_context = _untainted
        for value in ("false", "no", None, 1):
            release = threading.Event()
            results = {}

            def slow_handler(name, summary):
                release.wait(timeout=10)
                return True

            confirm_gate.register_handler(slow_handler)
            thread = threading.Thread(
                target=lambda: results.__setitem__(
                    "r", confirm_gate.request_confirmation(
                        "test_note_tool", {"x": 1}, timeout_s=10)),
                daemon=True)
            thread.start()
            pending = None
            for _ in range(100):
                pending = confirm_gate.pending_request()
                if pending is not None:
                    break
                release.wait(timeout=0.05)
            self.assertIsNotNone(pending, "request never became pending")
            with self.assertLogs("nova.confirm_gate",
                                 level="INFO") as logs:
                resolved = confirm_gate.resolve_pending(
                    pending[0], value, via="test")
            self.assertTrue(resolved, "live request must resolve")
            self.assertTrue(
                any("invalid resolve response" in line
                    for line in logs.output),
                f"{value!r} must be logged as invalid")
            release.set()
            thread.join(timeout=10)
            self.assertFalse(results.get("r"),
                             f"{value!r} must deny the request")
            confirm_gate.clear_handler()
        # Control: a literal True resolve still approves.
        release = threading.Event()
        results = {}

        def slow_handler2(name, summary):
            release.wait(timeout=10)
            return True

        confirm_gate.register_handler(slow_handler2)
        thread = threading.Thread(
            target=lambda: results.__setitem__(
                "r", confirm_gate.request_confirmation(
                    "test_note_tool", {"x": 1}, timeout_s=10)),
            daemon=True)
        thread.start()
        pending = None
        for _ in range(100):
            pending = confirm_gate.pending_request()
            if pending is not None:
                break
            release.wait(timeout=0.05)
        self.assertIsNotNone(pending)
        self.assertTrue(confirm_gate.resolve_pending(
            pending[0], True, via="test"))
        release.set()
        thread.join(timeout=10)
        self.assertTrue(results.get("r"))


class TestP17SingleInvocation(_A1Base):
    def test_typeerror_after_work_invoked_once_3arg(self):
        """P1-7: a 3-arg handler raising TypeError after doing work
        runs exactly once (old code retried with 2 args: [3, 2])."""
        confirm_gate._taint_context = _untainted
        work = []

        def handler(name, summary, request_id):
            work.append("did-work")
            raise TypeError("boom after work")

        result = self._ask("test_note_tool", {"x": 1}, handler)
        self.assertFalse(result)
        self.assertEqual(work, ["did-work"])

    def test_typeerror_after_work_invoked_once_2arg(self):
        """P1-7 legacy case: a 2-arg handler raising TypeError after
        doing work also runs exactly once."""
        confirm_gate._taint_context = _untainted
        work = []

        def handler(name, summary):
            work.append("did-work")
            raise TypeError("boom after work")

        result = self._ask("test_note_tool", {"x": 1}, handler)
        self.assertFalse(result)
        self.assertEqual(work, ["did-work"])

    def test_arity_adapted_at_registration(self):
        """2-arg, 3-arg and 3-arg-with-default handlers are each
        called with their declared arity, exactly once."""
        self.assertEqual(confirm_gate._handler_arity_for(
            lambda n, s: True), 2)
        self.assertEqual(confirm_gate._handler_arity_for(
            lambda n, s, r: True), 3)
        self.assertEqual(confirm_gate._handler_arity_for(
            lambda n, s, r=None: True), 3)
        seen = []

        def handler3(name, summary, request_id):
            seen.append(("3", request_id))
            return True

        confirm_gate._taint_context = _untainted
        self.assertTrue(self._ask("test_note_tool", {"x": 1},
                                  handler3))
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0][1], int)


if __name__ == "__main__":
    unittest.main()
