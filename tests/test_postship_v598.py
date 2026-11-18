"""Post-ship v5.98 follow-ups — the two disclosed gate leavers.

1. GroqSession voice_ready parity (battery v515_groq): Phase 1 C05
   added voice_ready to local sessions only; the battery's dynamic
   surface-parity check requires it on GroqSession too. Groq text
   carries no STT ears in the session object, so False is the
   truthful value — mirroring local's property shape (property,
   same name, same semantics: voice actually available in this
   session object).
2. v548_p28 precedence ruling (PARENT RULING, decided, not open):
   the Phase 2 C10 unknown-outcome guard correctly takes precedence
   over the cool-down check — re-execution safety outranks
   cool-down messaging, because repeating a call whose prior
   outcome is unknown is unsafe regardless of cool-down state.
   The funnel already checks the guard BEFORE the cool-down
   (guard block at the top of ToolRegistry.execute(), cool-down
   sites further down the same path, native + MCP) — so no
   production change is needed here; these tests PROVE the order
   from behaviour, and the battery p28 expectation was updated
   to the guard's verify-first refusal with a comment naming
   this ruling. The cool-down still applies once the prior
   outcome is known (resolved receipt) — pinned below, with the
   handler never re-running in either case.

Hermetic: temp receipt store (install_store), clean budget window
via the L3 shared helper, config fields saved/restored, confirm
handler saved/restored, taint cleared on entry and exit. Data
restore rule (C10): this suite writes timeout receipts to a TEMP
store, never the real data file, so it cannot poison later runs.
"""

import tempfile
import time
import unittest
from pathlib import Path

try:
    from _budget_isolation import restore_budget, snapshot_budget
except ImportError:  # unittest run from the tree root
    from tests._budget_isolation import (  # type: ignore
        restore_budget, snapshot_budget)

from core.voice import cloud_text_client  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core import confirm_gate
from core import injection
from core import tool_receipts
from core.funnel import tools_handlers as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.config import config as cfg
from core.voice.local_client import LocalLiveSession  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.funnel.tools_registry import ToolRegistry  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)

_BUDGET_SNAPSHOT = None


def setUpModule():
    global _BUDGET_SNAPSHOT
    _BUDGET_SNAPSHOT = snapshot_budget()


def tearDownModule():
    restore_budget(_BUDGET_SNAPSHOT)


class TestGroqVoiceReady(unittest.TestCase):
    """Battery v515_groq surface parity: voice_ready on Groq."""

    def test_groq_exposes_voice_ready_property_false(self):
        prop = getattr(cloud_text_client.GroqSession, "voice_ready", None)
        self.assertIsInstance(
            prop, property,
            "voice_ready must be a property, mirroring local's shape")
        session = cloud_text_client.GroqSession()
        self.assertIs(session.voice_ready, False)

    def test_local_voice_ready_behaviour_unchanged(self):
        # Mirrors test_phase1's construction (no full session start).
        session = LocalLiveSession.__new__(LocalLiveSession)
        session._whisper = None
        self.assertFalse(session.voice_ready)
        session._whisper = object()
        self.assertTrue(session.voice_ready)


class TestGuardBeforeCooldown(unittest.TestCase):
    """C10 guard precedes the cool-down check (v548_p28 ruling)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = tool_receipts.install_store(
            Path(self._tmp.name) / "receipts.jsonl")
        self.addCleanup(tool_receipts.reset_default)
        self._saved_cfg = {
            "tool_deadline_input_s": cfg.tool_deadline_input_s,
            "tool_timeout_cooldown_s": cfg.tool_timeout_cooldown_s,
        }
        cfg.tool_deadline_input_s = 0.3
        cfg.tool_timeout_cooldown_s = 30.0
        self._saved_handler = confirm_gate._handler
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        # The cool-down registry is module-global shared state
        # (like the budget window L3 isolated): a previous test's
        # timeout entry for the same target would make this
        # test's first dispatch come back cool-down-refused
        # instead of timing out — same kind, no dispatch, silent
        # misclassification. Clear it on entry and exit, and use
        # distinct targets per test below, so every test starts
        # from a clean registry.
        tools_mod._timeout_cooldowns.clear()
        self.calls = {"n": 0}
        self.sleeping = {"on": True}

        def slow_type(path: str = "") -> str:
            self.calls["n"] += 1
            if self.sleeping["on"]:
                time.sleep(60)
            return "typed-ok"

        self.reg = ToolRegistry()
        self.reg.register(
            name="type_text", description="spy (input class)",
            parameters={"type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": []},
            handler=slow_type)

    def tearDown(self):
        for key, value in self._saved_cfg.items():
            setattr(cfg, key, value)
        if self._saved_handler is None:
            confirm_gate.clear_handler()
        else:
            confirm_gate.register_handler(self._saved_handler)
        injection.clear_taint(force=True)
        tools_mod._timeout_cooldowns.clear()

    def _timeout_once(self, target):
        before = self.calls["n"]
        res = self.reg.execute("type_text", {"path": target})
        self.assertEqual(res.get("kind"), "timeout",
                         f"expected a funnel timeout, got {res!r}")
        # A cool-down refusal shares kind "timeout" but never
        # dispatches — assert the dispatch actually happened, so
        # a stale registry entry cannot silently stand in for a
        # real timeout.
        self.assertNotIn("cooling down", str(res.get("message")))
        self.assertEqual(self.calls["n"], before + 1,
                         "timeout leg must dispatch exactly once")
        return res

    def test_unknown_repeat_is_guard_refused_not_cooldown(self):
        # The timeout seeds BOTH an unknown receipt and a cool-down
        # entry; the repeat must come back as the guard's
        # verify-first refusal (kind refused), NOT the cool-down's
        # (kind timeout + "cooling down") — proving the guard runs
        # first. The handler must never re-run.
        self._timeout_once("C:/a.txt")
        self.assertEqual(self.calls["n"], 1)
        res = self.reg.execute("type_text", {"path": "C:/a.txt"})
        self.assertEqual(res.get("status"), "refused")
        self.assertEqual(res.get("kind"), "refused")
        self.assertIn("unknown outcome", str(res.get("message")))
        self.assertIn("not re-executed", str(res.get("message")))
        self.assertNotIn("cooling down", str(res.get("message")))
        self.assertEqual(self.calls["n"], 1,
                         "guard refusal must not re-dispatch")

    def test_cooldown_applies_once_prior_is_known(self):
        # Same timeout, then the prior outcome becomes KNOWN
        # (late finalize resolves the receipt to a failure) — the
        # guard goes clean, and the repeat must now come back as
        # the cool-down refusal, still without re-running.
        self._timeout_once("C:/b.txt")
        prior = self.store.find_unknown(
            tool="type_text", args={"path": "C:/b.txt"})
        self.assertIsNotNone(prior, "timeout must leave an unknown")
        self.store.record_outcome(prior["receipt_id"], status="failed",
                                  summary="late-known: handler failed")
        self.assertIsNone(self.store.find_unknown(
            tool="type_text", args={"path": "C:/b.txt"}))
        res = self.reg.execute("type_text", {"path": "C:/b.txt"})
        self.assertEqual(res.get("kind"), "timeout")
        self.assertIn("cooling down", str(res.get("message")))
        self.assertNotIn("unknown outcome", str(res.get("message")))
        self.assertEqual(self.calls["n"], 1,
                         "cool-down refusal must not re-dispatch")

    def test_guard_and_cooldown_are_per_target(self):
        # A different target has no unknown receipt and no
        # cool-down entry: it dispatches (and times out on its
        # own), proving neither refusal leaks across targets.
        self._timeout_once("C:/c.txt")
        res = self.reg.execute("type_text", {"path": "C:/d.txt"})
        self.assertEqual(res.get("kind"), "timeout")
        self.assertEqual(self.calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
