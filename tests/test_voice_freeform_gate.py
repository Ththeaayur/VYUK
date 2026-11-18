"""Voice free-form loop gate tests (2026-11-03 batch).

Reconciliation, pinned: the bridge + coordinator adoption DO flow end
to end when a text brain is injected (test_mechanism_flows_when_brain
_injected proves it with a canned brain), AND the programme record
calling the loop a build gap is ALSO true in the shipped app — in
Gemini mode no text brain can arrive through production wiring, so
a freely spoken free-form goal dead-ends at the honest refusal even
with the bridge toggled ON. The gate constant
(VOICE_FREEFORM_COMPANION_GATE) states that precisely; these tests
pin the production shape that proves it, the refusal it produces,
and the mechanism that still flows once the precondition is met.

Before-fix behaviour, as found: bridge ON + no text brain (the
production Gemini shape) -> hand_off None -> run_bridged None ->
session.run_goal refusal, no task created. After this batch: that
behaviour is UNCHANGED (it is the honest shipped path), but it is
now a named gate in code instead of an undocumented dead end, and
the tests pin both the refusal and the gate text so the seam
cannot drift silently back into "the loop exists" shorthand.

Hermetic like test_voice_adoption: a REAL unconnected
GeminiLiveSession, a canned stub text brain (no planner loop, no
tools, no network), a temp task ledger, and a stubbed plan store
(loads return [], saves are no-ops) so no test plan can match an
ambient plan or land in the real data/ store. Bridge global state
(enabled + injection) is snapshotted per test and restored, so
the rest of the run sees exactly the state it would have seen
without this suite.
"""

import tempfile
import unittest
from pathlib import Path

from core import brain_capabilities as bc
from core.voice import voice_text_bridge as bridge
from core.brain import planner as planner_mod
from core.brain import planner_checkpoint, planner_plans, planner_routines  # INTENTIONAL (skills split B41): impl modules for dual-patching (see setUp notes below)
from core.brain import voice_goal as vg
from core.brain.task_ledger import TaskLedger
from core.voice.live_client import (
    GeminiLiveSession,
    live_session as gemini_singleton,
)

_SAVED = {}


def setUpModule():
    _SAVED["bridge_on"] = bridge.is_voice_bridge_enabled()
    _SAVED["bridge_brain"] = bridge.get_text_brain()


def tearDownModule():
    if _SAVED["bridge_on"]:
        bridge.enable_voice_bridge()
    else:
        bridge.disable_voice_bridge()
    bridge.set_text_brain(_SAVED["bridge_brain"])


class CannedBrain:
    """A text brain that runs nothing: run_goal returns a canned
    completed report. Proves the adoption mechanism flows once a
    brain is injected, without a planner loop, tools, or network."""

    def __init__(self, report="Goal complete: done"):
        self.report = report
        self.calls = []

    async def run_goal(self, goal_text):
        self.calls.append(goal_text)
        return self.report


class VoiceFreeformGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig_bridge_on = bridge.is_voice_bridge_enabled()
        self._orig_bridge_brain = bridge.get_text_brain()
        self._orig_load_plans = planner_mod._load_plans
        planner_mod._load_plans = lambda: ([], 0)
        # INTENTIONAL (skills split B41): _load_plans/_save_list are bound
        # in the impl modules too (snapshot imports); patch every binding
        # with the same fake, or moved callers would miss it.
        planner_plans._load_plans = planner_mod._load_plans
        planner_checkpoint._load_plans = planner_mod._load_plans
        self._orig_save_list = planner_mod._save_list
        planner_mod._save_list = lambda path, data: True
        planner_plans._save_list = planner_mod._save_list
        planner_routines._save_list = planner_mod._save_list
        planner_checkpoint._save_list = planner_mod._save_list
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        bridge.disable_voice_bridge()
        bridge.set_text_brain(None)

    def tearDown(self):
        planner_mod._load_plans = self._orig_load_plans
        planner_plans._load_plans = self._orig_load_plans
        planner_checkpoint._load_plans = self._orig_load_plans
        planner_mod._save_list = self._orig_save_list
        planner_plans._save_list = self._orig_save_list
        planner_routines._save_list = self._orig_save_list
        planner_checkpoint._save_list = self._orig_save_list
        if self._orig_bridge_on:
            bridge.enable_voice_bridge()
        else:
            bridge.disable_voice_bridge()
        bridge.set_text_brain(self._orig_bridge_brain)

    def _session(self):
        session = GeminiLiveSession()
        session._running = True
        session.on_transcript = lambda role, text: None
        session._voice_goal_ledger = TaskLedger(
            path=str(Path(self._tmp.name) / "task_ledger.json"))
        return session

    # -- the gate, as written in code --

    def test_gate_constant_exported_and_worded(self):
        self.assertIn("VOICE_FREEFORM_COMPANION_GATE", bridge.__all__)
        gate = bridge.VOICE_FREEFORM_COMPANION_GATE
        self.assertIsInstance(gate, str)
        self.assertIn("GATED, not built", gate)
        # The missing piece + each unblocker, by name.
        self.assertIn("text brain", gate)
        self.assertIn("Groq", gate)
        self.assertIn("local", gate)
        self.assertIn("genai", gate)
        self.assertIn("LiteLiveSession has no run_goal", gate)
        # The shipped path until an unblocker lands.
        self.assertIn("saved plans", gate)
        self.assertIn("run_plan", gate)
        self.assertIn("refuse honestly", gate)

    def test_gate_named_in_module_docstring(self):
        self.assertIn("VOICE_FREEFORM_COMPANION_GATE", bridge.__doc__)

    # -- the production shape: Gemini mode can never attach a brain --

    def test_gemini_singleton_cannot_attach(self):
        # The shipped app binds this singleton as its only inner
        # session (brain.py start()); the attach guard refuses it
        # by type name, exactly as the toggle's identity skip
        # would. No brain is injected by the attempt.
        self.assertFalse(
            bridge.attach_text_brain_candidate(gemini_singleton))
        self.assertIsNone(bridge.get_text_brain())

    def test_non_voice_brain_attaches(self):
        # Sanity that the guard is specific, not blanket: a real
        # text brain (callable run_goal, not a voice session)
        # attaches fine.
        brain = CannedBrain()
        try:
            self.assertTrue(bridge.attach_text_brain_candidate(brain))
            self.assertIs(bridge.get_text_brain(), brain)
        finally:
            bridge.set_text_brain(None)

    # -- the dead end, as found (bridge ON, no brain) --

    async def test_bridge_on_no_brain_hand_off_none(self):
        bridge.enable_voice_bridge()
        session = self._session()
        self.assertIsNone(
            await bridge.hand_off(session, "do something free-form"))

    async def test_production_shape_run_goal_refuses(self):
        # The exact production Gemini shape, end to end through
        # the real entry: bridge toggled ON, but no text brain
        # (the toggle could not attach one) -> the honest
        # refusal, byte-identical in spirit with the bridge off,
        # and no coordinator task is created for a goal that
        # never ran.
        bridge.enable_voice_bridge()
        session = self._session()
        result = await session.run_goal("do something free-form")
        self.assertIn("can't run a free-form goal loop", result)
        self.assertFalse(hasattr(session, "_voice_goal_task"))
        self.assertFalse(bc.can_freeform_goal_now("gemini_live"))

    async def test_no_task_for_unrunnable_goal_via_bridge_entry(self):
        # Same dead end through the adoption entry directly: None
        # back, no task, so the caller falls through to refusal.
        bridge.enable_voice_bridge()
        session = self._session()
        self.assertIsNone(
            await vg.run_bridged_voice_goal(session, "do something"))
        self.assertFalse(hasattr(session, "_voice_goal_task"))

    async def test_bridge_off_refuses_regardless_of_brain(self):
        # The two preconditions are independent ANDs: a brain
        # without the flag still refuses.
        bridge.disable_voice_bridge()
        bridge.set_text_brain(CannedBrain())
        session = self._session()
        self.assertIsNone(
            await bridge.hand_off(session, "do something free-form"))

    # -- the capability contract's runtime answer across the matrix --

    def test_capability_matrix(self):
        brain = CannedBrain()
        bridge.enable_voice_bridge()
        bridge.set_text_brain(brain)
        self.assertTrue(bc.can_freeform_goal_now("gemini_live"))
        bridge.set_text_brain(None)
        self.assertFalse(bc.can_freeform_goal_now("gemini_live"))
        bridge.disable_voice_bridge()
        bridge.set_text_brain(brain)
        self.assertFalse(bc.can_freeform_goal_now("gemini_live"))
        self.assertIsNone(bc.can_freeform_goal_now(None))
        self.assertIsNone(bc.can_freeform_goal_now("no_such_brain"))

    # -- the mechanism flows once the precondition IS met --

    async def test_mechanism_flows_when_brain_injected(self):
        # The gate is about the precondition, not the mechanism:
        # inject a brain and the bridged goal completes as a
        # coordinator task with a receipt, exactly as the adoption
        # batch built it.
        bridge.enable_voice_bridge()
        brain = CannedBrain("Goal complete: done")
        bridge.set_text_brain(brain)
        session = self._session()
        result = await vg.run_bridged_voice_goal(
            session, "do something free-form")
        self.assertIsNotNone(result)
        self.assertIn("Goal complete", result)
        self.assertIn("Voice goal receipt: task", result)
        self.assertEqual(brain.calls, ["do something free-form"])
        receipt = session._voice_goal_receipt
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.state, "completed")


if __name__ == "__main__":
    unittest.main()
