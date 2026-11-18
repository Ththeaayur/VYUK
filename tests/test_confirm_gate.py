"""Shipped unit tests (v5.45, P-1): confirm_gate fail-closed.

The gate's contract is DEFAULT-DENY: no handler -> deny, timeout
-> deny, handler error -> deny, handler says no -> deny; only an
explicit handler yes approves. Decision logging is silenced via
the module's own _log_decision seam so the run does not append to
the user's real data/activity.log.

Stdlib unittest only. Run via tests/run_tests.py.
"""

from __future__ import annotations

import time
import unittest

try:
    from core import confirm_gate
except Exception as exc:  # noqa: BLE001
    raise unittest.SkipTest(f"core.confirm_gate unavailable: {exc}")


class FailClosedTests(unittest.TestCase):
    def setUp(self):
        confirm_gate.clear_handler()
        self._orig_log = confirm_gate._log_decision
        confirm_gate._log_decision = lambda *a, **k: None

    def tearDown(self):
        confirm_gate.clear_handler()
        confirm_gate._log_decision = self._orig_log

    def test_no_handler_denies(self):
        self.assertFalse(confirm_gate.has_handler())
        self.assertFalse(
            confirm_gate.request_confirmation("run_command",
                                              {"command": "echo hi"}))

    def test_handler_yes_approves(self):
        confirm_gate.register_handler(lambda name, summary: True)
        self.assertTrue(
            confirm_gate.request_confirmation("run_command",
                                              {"command": "echo hi"}))

    def test_handler_no_denies(self):
        confirm_gate.register_handler(lambda name, summary: False)
        self.assertFalse(
            confirm_gate.request_confirmation("run_command",
                                              {"command": "echo hi"}))

    def test_handler_error_denies(self):
        def boom(name, summary):
            raise RuntimeError("popup exploded")

        confirm_gate.register_handler(boom)
        self.assertFalse(
            confirm_gate.request_confirmation("run_command",
                                              {"command": "echo hi"}))

    def test_timeout_denies(self):
        def slow(name, summary):
            time.sleep(2.0)
            return True

        confirm_gate.register_handler(slow)
        started = time.monotonic()
        self.assertFalse(
            confirm_gate.request_confirmation(
                "run_command", {"command": "echo hi"}, timeout_s=0.2))
        self.assertLess(time.monotonic() - started, 1.5)

    def test_handler_receives_masked_summary(self):
        seen = {}

        def capture(name, summary):
            seen["name"] = name
            seen["summary"] = summary
            return True

        confirm_gate.register_handler(capture)
        confirm_gate.request_confirmation(
            "some_tool", {"api_key": "supersecretvalue", "path": "a.txt"})
        self.assertEqual(seen["name"], "some_tool")
        self.assertNotIn("supersecretvalue", seen["summary"])
        self.assertIn("path=a.txt", seen["summary"])

    def test_summarize_masks_and_handles_empty(self):
        self.assertEqual(confirm_gate.summarize_args(None), "(no arguments)")
        self.assertEqual(confirm_gate.summarize_args({}), "(no arguments)")
        masked = confirm_gate.summarize_args({"password": "hunter2"})
        self.assertNotIn("hunter2", masked)


if __name__ == "__main__":
    unittest.main()
