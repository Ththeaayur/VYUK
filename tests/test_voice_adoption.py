"""Voice-brain adoption tests (voice free-form goal loop).

The voice brain runs a free-form goal as a coordinator task whose
planning comes from an injected text brain through the Wave 1
bridge (core/brain/voice_goal.py). These tests drive a REAL unconnected
GeminiLiveSession (no live Gemini — the headless limit, stated in
the module docstring of voice_goal.py and in the batch brief), a
stub text brain whose run_goal calls the REAL planner.run_goal
with a stub picker, and the REAL funnel (tools.execute) with the
REAL confirm gate. Only the leaves are stubbed: the picker (the
model) and, in the gate tests, the mail-sending leaf handler of
send_email — the gate itself is never stubbed.

Global-state hygiene follows the testiso guest rule: budget
timestamps, budget clock, confirm handler, and bridge flag +
injection are snapshotted in setUpModule and restored in
tearDownModule; each test opens a clean budget window in setUp
and restores in tearDown, so the rest of the full run sees exactly
the state it would have seen without this suite. The plan store
is hermetic too (loads return [], saves are no-ops): run_plan
persists progress, and a test plan must never land in the real
data/ store or match an ambient plan.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from core import brain_capabilities as bc
from core import confirm_gate
from core.brain import planner as planner_mod  # Phase 3 B2-remainder INTENTIONAL: planner moved to core/brain/
from core.brain import planner_checkpoint, planner_plans, planner_routines  # INTENTIONAL (skills split B41): impl modules for dual-patching (see setUp notes below)
from core.funnel import tools_handlers as th  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.voice import voice_text_bridge as bridge
from core.brain import voice_goal as vg  # Phase 3 B1b INTENTIONAL: voice_goal moved to core/brain/
from core.voice.live_client import GeminiLiveSession  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.brain.task_coordinator import TurnCoordinator  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.brain.task_ledger import TaskLedger  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.funnel.tools_registry import tools as live_registry  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)

REFUSAL = (
    "I can't run a free-form goal loop on the Live brain: "
    "this session is model-paced audio with no text "
    "picker to drive one action at a time, so nothing was "
    "run. Free-form goal loops run on the text brains "
    "(the local brain or Groq) — switch brain and ask "
    "again there. Or save this goal as a plan whose steps "
    "declare their tools, and I can run that plan here "
    "with run_plan."
)


def _save_handler():
    return confirm_gate._handler


def _restore_handler(saved):
    if saved is None:
        confirm_gate.clear_handler()
    else:
        confirm_gate.register_handler(saved)


_SAVED = {}


def setUpModule():
    _SAVED["budget"] = list(th._budget_timestamps)
    _SAVED["now"] = th._budget_now
    _SAVED["handler"] = _save_handler()
    _SAVED["bridge_on"] = bridge.is_voice_bridge_enabled()
    _SAVED["bridge_brain"] = bridge.get_text_brain()
    th.reset_tool_budget()


def tearDownModule():
    th._budget_timestamps[:] = _SAVED["budget"]
    th._budget_now = _SAVED["now"]
    _restore_handler(_SAVED["handler"])
    if _SAVED["bridge_on"]:
        bridge.enable_voice_bridge()
    else:
        bridge.disable_voice_bridge()
    bridge.set_text_brain(_SAVED["bridge_brain"])


def _changing_observe():
    """Zero-arg observe whose value changes every call, so the
    planner's verification-by-change always sees progress."""
    tick = [0]

    def _observe():
        tick[0] += 1
        return {"tick": tick[0]}

    return _observe


class StubBrain:
    """A text brain: run_goal drives the REAL planner loop with a
    stub picker (the model). Forwards interruption_check, so the
    voice wrapper's cancel/session-stop check reaches the loop's
    step boundaries exactly as a shipped text brain's does."""

    def __init__(self, picker):
        self.picker = picker
        self.calls = []
        self.seen_checks = []

    async def run_goal(self, goal_text, interruption_check=None):
        self.calls.append(goal_text)
        self.seen_checks.append(interruption_check)
        return await planner_mod.run_goal(
            goal_text, pick_action=self.picker,
            observe=_changing_observe(),
            interruption_check=interruption_check)


class LegacyBrain:
    """A text brain whose run_goal does NOT accept an
    interruption_check — the legacy path: the loop runs without
    voice-side interruption, and the receipt must say so."""

    def __init__(self, picker):
        self.picker = picker
        self.calls = []

    async def run_goal(self, goal_text):
        self.calls.append(goal_text)
        return await planner_mod.run_goal(
            goal_text, pick_action=self.picker,
            observe=_changing_observe())


def _read_then_done(picker_calls):
    """Picker: one real read step, then done."""
    def _pick(goal, observation, history):
        picker_calls.append(goal)
        if len(picker_calls) == 1:
            return {"tool": "get_current_time_date", "args": {}}
        return {"done": "checked the time"}
    return _pick


class VoiceAdoptionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        th.reset_tool_budget()
        self._orig_now = th._budget_now
        self._orig_handler = _save_handler()
        self._orig_bridge_on = bridge.is_voice_bridge_enabled()
        self._orig_bridge_brain = bridge.get_text_brain()
        confirm_gate.clear_handler()
        # Hermetic plan store: voice-goal tests must never match a
        # real plan from the ambient data/ store (the substring
        # matcher would otherwise catch an ambient plan for
        # ordinary goal texts) — and run_plan PERSISTS progress, so
        # the save path is stubbed too: a test plan must never land
        # in the real store (an earlier draft of this suite did
        # exactly that, creating data/plans.json mid-run and
        # poisoning later tests — fixed here, not hidden). The
        # saved-plan test installs its own loader.
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

    def tearDown(self):
        th._budget_now = self._orig_now
        th.reset_tool_budget()
        _restore_handler(self._orig_handler)
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
        transcripts = []
        session.on_transcript = lambda role, text: transcripts.append(
            (role, text))
        session._voice_goal_ledger = TaskLedger(
            path=str(Path(self._tmp.name) / "task_ledger.json"))
        return session, transcripts

    def _enable(self, brain):
        bridge.enable_voice_bridge()
        bridge.set_text_brain(brain)

    # -- report classification (pure, fail-closed) --

    def test_classify_complete(self):
        state, kind, msg = vg.classify_voice_goal_report(
            "Goal complete: checked the time", interrupted=False)
        self.assertEqual((state, kind), ("completed", ""))
        self.assertIn("Goal complete", msg)

    def test_classify_finished(self):
        state, _, _ = vg.classify_voice_goal_report(
            "Goal finished: 2 action(s) completed in 2 executed action(s).",
            interrupted=False)
        self.assertEqual(state, "completed")

    def test_classify_halt(self):
        state, kind, msg = vg.classify_voice_goal_report(
            "Goal halted at step 1: gated.", interrupted=False)
        self.assertEqual((state, kind), ("failed", "halted"))
        self.assertIn("Goal halted", msg)

    def test_classify_interrupted_beats_complete(self):
        # A fired interruption is cancelled whatever the report says.
        state, kind, _ = vg.classify_voice_goal_report(
            "Goal complete: done", interrupted=True)
        self.assertEqual((state, kind), ("cancelled", "cancelled"))

    def test_classify_unknown_fails_closed(self):
        state, kind, _ = vg.classify_voice_goal_report(
            "some unrecognised report text", interrupted=False)
        self.assertEqual((state, kind), ("failed", "unknown"))

    def test_classify_per_step_halt(self):
        # A gated/refused action surfaces as a per-step halt line,
        # not a "Goal halted:" line — the classifier must read it
        # as a halt, not an unknown report.
        state, kind, msg = vg.classify_voice_goal_report(
            "Goal step 1: send_email [send_email]\n"
            "  ✗ Goal step 1 [send_email] — halted: 'send_email' is "
            "a critical action and needs the user's OK first. "
            "The goal stops here.",
            interrupted=False)
        self.assertEqual((state, kind), ("failed", "halted"))
        self.assertIn("halted", msg)

    def test_classify_bridge_error_fails_closed(self):
        state, kind, _ = vg.classify_voice_goal_report(
            "The text loop failed on this goal (boom), so the goal "
            "did not complete. Nothing was claimed as done.",
            interrupted=False)
        self.assertEqual((state, kind), ("failed", "unknown"))

    # -- end-to-end through the new loop --

    async def test_voice_goal_end_to_end(self):
        calls = []
        session, transcripts = self._session()
        self._enable(StubBrain(_read_then_done(calls)))
        result = await session.run_goal("voice adoption end-to-end probe")
        self.assertNotIn("can't run a free-form goal loop", result)
        self.assertIn("Goal complete", result)
        self.assertIn("Voice goal receipt: task", result)
        receipt = session._voice_goal_receipt
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.state, "completed")
        self.assertTrue(any("Voice goal completed" in t
                            for _, t in transcripts))
        task = session._voice_goal_task
        self.assertIsNotNone(task)
        self.assertEqual(task.state, "completed")
        # Ledger record exists in the temp ledger (never shipped data/).
        ledger_path = Path(self._tmp.name) / "task_ledger.json"
        self.assertTrue(ledger_path.exists())
        self.assertIn(task.id, ledger_path.read_text())
        # Mission conversion through the coordinator bridge.
        mission = session._voice_goal_coordinator.to_mission_receipt(task)
        self.assertEqual(mission.status, "completed")
        self.assertEqual(calls, ["voice adoption end-to-end probe"] * 2)

    async def test_saved_plan_delegation_unchanged(self):
        # Plan-first ordering survives adoption: with the bridge ON
        # and a stub brain injected, a goal naming a saved plan
        # still runs that plan's DECLARED steps, never the bridge.
        calls = []
        brain = StubBrain(_read_then_done(calls))
        session, transcripts = self._session()
        self._enable(brain)

        def _plans():
            return ([{"id": "plan-9", "goal": "check the time",
                      "steps": [{"tool": "get_current_time_date",
                                 "args": {}, "text": "check the time"}]}],
                    0)

        orig_load = planner_mod._load_plans
        planner_mod._load_plans = _plans
        # INTENTIONAL (skills split B41): same dual-patch as setUp — the
        # driven run_plan resolves _load_plans from checkpoint globals.
        planner_plans._load_plans = _plans
        planner_checkpoint._load_plans = _plans
        try:
            result = await session.run_goal("please run plan-9 now")
        finally:
            planner_mod._load_plans = orig_load
            planner_plans._load_plans = orig_load
            planner_checkpoint._load_plans = orig_load
        self.assertIn("Running saved plan plan-9", result)
        self.assertEqual(brain.calls, [])  # bridge never ran
        self.assertIsNone(getattr(session, "_voice_goal_task", None))

    async def test_bridge_off_refuses_byte_identical(self):
        session, transcripts = self._session()
        bridge.disable_voice_bridge()
        result = await session.run_goal("do something free-form")
        self.assertEqual(result, REFUSAL)
        self.assertEqual(transcripts[-1][1], REFUSAL)
        self.assertIsNone(getattr(session, "_voice_goal_task", None))

    async def test_bridge_on_no_brain_refuses(self):
        session, _ = self._session()
        bridge.enable_voice_bridge()
        bridge.set_text_brain(None)
        result = await session.run_goal("do something free-form")
        self.assertEqual(result, REFUSAL)

    async def test_voice_brain_as_own_text_brain_refuses(self):
        # A mis-injection can never recurse: the voice session as
        # its own text brain refuses exactly as no brain would.
        session, _ = self._session()
        bridge.enable_voice_bridge()
        bridge.set_text_brain(session)
        result = await session.run_goal("do something free-form")
        self.assertEqual(result, REFUSAL)

    # -- C-14 through the voice goal (gate real, leaf stubbed) --

    def _patch_send_email_leaf(self, ran):
        """Swap ONLY the mail-sending leaf handler for a probe; the
        confirm gate, validation, budget, and funnel stay real.
        Returns the saved entry for restoration."""
        entry = live_registry._tools["send_email"]
        saved = dict(entry)
        entry["handler"] = lambda **kw: ran.append(kw) or {"ok": True}
        return saved

    async def test_gated_step_refused_without_approval(self):
        ran = []
        saved = self._patch_send_email_leaf(ran)
        try:
            calls = []

            def _pick(goal, observation, history):
                calls.append(goal)
                if len(calls) == 1:
                    return {"tool": "send_email",
                            "args": {"to": "probe@example.com",
                                     "subject": "probe",
                                     "body": "probe"}}
                return {"done": "sent"}
            session, _ = self._session()
            self._enable(StubBrain(_pick))
            result = await session.run_goal("send a probe mail")
            self.assertEqual(ran, [])  # handler never ran
            receipt = session._voice_goal_receipt
            self.assertEqual(receipt.state, "failed")
            # The refusal surfaces as the planner's per-step halt
            # line ("— halted:"), not a "Goal halted:" line.
            self.assertIn("halted", result)
        finally:
            live_registry._tools["send_email"] = saved

    async def test_gated_step_runs_with_approval(self):
        ran = []
        saved = self._patch_send_email_leaf(ran)
        try:
            confirm_gate.register_handler(lambda *a, **k: True)
            calls = []

            def _pick(goal, observation, history):
                calls.append(goal)
                if len(calls) == 1:
                    return {"tool": "send_email",
                            "args": {"to": "probe@example.com",
                                     "subject": "probe",
                                     "body": "probe"}}
                return {"done": "sent"}
            session, _ = self._session()
            self._enable(StubBrain(_pick))
            result = await session.run_goal("send a probe mail")
            self.assertEqual(len(ran), 1)
            self.assertIn("Goal complete", result)
            self.assertEqual(session._voice_goal_receipt.state,
                             "completed")
        finally:
            live_registry._tools["send_email"] = saved

    # -- cancellation --

    async def test_cancellation_stops_remaining_steps(self):
        calls = []
        session, transcripts = self._session()

        def _pick(goal, observation, history):
            calls.append(goal)
            if len(calls) == 2:
                # Cancel during the second pick: the loop halts at
                # the next step boundary, so picks 3..10 never run.
                vg.cancel_voice_goal(session)
            if len(calls) >= 10:
                return {"done": "safety net"}
            return {"tool": "get_current_time_date", "args": {}}
        self._enable(StubBrain(_pick))
        result = await session.run_goal("keep checking the time")
        self.assertEqual(session._voice_goal_receipt.state, "cancelled")
        self.assertIn("interrupted", result)
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("Voice goal cancelled" in t
                            for _, t in transcripts))

    async def test_session_stop_cancels_goal(self):
        calls = []
        session, _ = self._session()

        def _pick(goal, observation, history):
            calls.append(goal)
            if len(calls) == 1:
                session._running = False
            if len(calls) >= 10:
                return {"done": "safety net"}
            return {"tool": "get_current_time_date", "args": {}}
        self._enable(StubBrain(_pick))
        result = await session.run_goal("keep checking the time")
        self.assertEqual(session._voice_goal_receipt.state, "cancelled")
        self.assertIn("interrupted", result)
        self.assertEqual(len(calls), 1)

    async def test_cancel_voice_goal_no_task_is_false(self):
        session, _ = self._session()
        self.assertFalse(vg.cancel_voice_goal(session))

    async def test_cancel_voice_goal_terminal_is_false(self):
        calls = []
        session, _ = self._session()
        self._enable(StubBrain(_read_then_done(calls)))
        await session.run_goal("voice adoption terminal probe")
        self.assertEqual(session._voice_goal_receipt.state, "completed")
        self.assertFalse(vg.cancel_voice_goal(session))

    # -- legacy brain: no interruption param, honest receipt --

    async def test_legacy_brain_best_effort(self):
        calls = []
        session, transcripts = self._session()
        self._enable(LegacyBrain(_read_then_done(calls)))
        result = await session.run_goal("voice adoption legacy probe")
        self.assertIn("Goal complete", result)
        self.assertEqual(session._voice_goal_receipt.state, "completed")
        self.assertTrue(any("best-effort" in t for _, t in transcripts))

    # -- contract conditionality + routing --

    def test_contract_static_and_runtime(self):
        contract = bc.contract_for("gemini_live")
        # Static declaration is True WITH the condition — never a
        # blanket YES without it (batch failure if conditionless).
        self.assertIs(bc.can_freeform_goal("gemini_live"), True)
        self.assertIsNotNone(contract.freeform_goal_condition)
        self.assertIn("bridge", contract.freeform_goal_condition)
        # Runtime: OFF -> False; ON + brain -> True; ON + none -> False.
        bridge.disable_voice_bridge()
        self.assertIs(bc.can_freeform_goal_now("gemini_live"), False)
        bridge.enable_voice_bridge()
        bridge.set_text_brain(None)
        self.assertIs(bc.can_freeform_goal_now("gemini_live"), False)
        bridge.set_text_brain(StubBrain(_read_then_done([])))
        self.assertIs(bc.can_freeform_goal_now("gemini_live"), True)
        # Other brains and unknown ids are unchanged.
        self.assertIs(bc.can_freeform_goal_now("local_text"), True)
        self.assertIs(bc.can_freeform_goal_now("groq_text"), True)
        self.assertIsNone(bc.can_freeform_goal_now("bogus"))
        self.assertIsNone(bc.can_freeform_goal_now(None))

    def test_routing_reflects_runtime_capability(self):
        coord = TurnCoordinator()
        text = "step 1 check the time, step 2 read it back"
        bridge.disable_voice_bridge()
        res = coord.handle_turn(text, plan_names=[], routine_names=[],
                                plan_steps_for=None, routine_steps_for=None,
                                brain_id="gemini_live")
        self.assertFalse(res["handled"])
        self.assertIs(res["routing"]["live_brain_freeform_goal"], False)
        bridge.enable_voice_bridge()
        bridge.set_text_brain(StubBrain(_read_then_done([])))
        res = coord.handle_turn(text, plan_names=[], routine_names=[],
                                plan_steps_for=None, routine_steps_for=None,
                                brain_id="gemini_live")
        self.assertIs(res["routing"]["live_brain_freeform_goal"], True)
        res = coord.handle_turn(text, plan_names=[], routine_names=[],
                                plan_steps_for=None, routine_steps_for=None,
                                brain_id="local_text")
        self.assertIs(res["routing"]["live_brain_freeform_goal"], True)


if __name__ == "__main__":
    unittest.main()
