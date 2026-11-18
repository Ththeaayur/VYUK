"""Wiring Build 1 tests (stdlib unittest, no GUI, no network, no model).

The Settings toggle itself is Qt (gui/window_settings.py, copied
from the wake toggle's pattern) and cannot run in this lab — the
lab has no WebEngine and libEGL is missing, so no GUI render is
claimed here. What IS tested, Qt-free, is everything the toggle
drives:

- toggle on enables the bridge flag + the hand_off path is
  reachable through the real live_client seam (GeminiLiveSession
  .run_goal with a free-form, plan-less goal);
- toggle off disables the flag + the same seam refuses honestly
  and the text brain is never called;
- the toggle's attach helper attaches a usable text brain and
  refuses voice brains, brainless objects, and None;
- a voice brain can never be its own text brain (no recursion);
- C-14 shape: the bridge forwards the loop's gate decision — a
  gated brain's refusal without approval passes through unchanged
  and no consequential action is performed, and with approval the
  brain's own decision (not the bridge's) performs it. The real
  gate stack itself is covered by tests/test_confirm_gate.py and
  tests/test_wave3_trust.py in the full suite; the bridge adds no
  gate and bypasses none, and it executes no tool itself.
- narration stays transcript-seam: the report arrives through
  on_transcript, and no audio path is added anywhere here.
"""

import asyncio
import unittest

from core.voice import voice_text_bridge as bridge
from core.provider_identity import Engine  # GAPS-22 (B-V3): engine identity
from core.voice.live_client import GeminiLiveSession  # Phase 3 V1b INTENTIONAL: moved to core/voice/


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class _FakeVoiceSession:
    def __init__(self):
        self.transcript = []

    def on_transcript(self, role, text):
        self.transcript.append((role, text))


class _FakeTextBrain:
    def __init__(self, report):
        self.report = report
        self.seen = []

    async def run_goal(self, goal_text):
        self.seen.append(goal_text)
        return self.report


class _GatedBrain:
    """A text brain with its own approve-first gate, standing in
    for planner.run_goal's gate stack: consequential goals refuse
    without approval and are never performed."""

    def __init__(self):
        self.approved = False
        self.performed = []

    async def run_goal(self, goal_text):
        if "delete" in goal_text.lower() and not self.approved:
            return "Refused: that action needs your approval first."
        self.performed.append(goal_text)
        return "Done: " + goal_text


class ToggleFlagTests(unittest.TestCase):
    def setUp(self):
        bridge.disable_voice_bridge()
        bridge.set_text_brain(None)

    def tearDown(self):
        bridge.disable_voice_bridge()
        bridge.set_text_brain(None)

    def test_toggle_on_enables_and_off_disables(self):
        self.assertFalse(bridge.is_voice_bridge_enabled())
        bridge.enable_voice_bridge()
        self.assertTrue(bridge.is_voice_bridge_enabled())
        bridge.disable_voice_bridge()
        self.assertFalse(bridge.is_voice_bridge_enabled())

    def test_toggle_off_never_calls_brain(self):
        session = _FakeVoiceSession()
        brain = _FakeTextBrain("Goal complete: done")
        bridge.set_text_brain(brain)
        # Toggle off: hand_off returns None, brain never called.
        self.assertIsNone(_run(bridge.hand_off(session, "do a thing")))
        self.assertEqual(brain.seen, [])

    def test_toggle_on_reaches_brain_with_exact_goal(self):
        session = _FakeVoiceSession()
        brain = _FakeTextBrain("Goal complete: opened calculator")
        bridge.enable_voice_bridge()
        bridge.set_text_brain(brain)
        result = _run(bridge.hand_off(session, "open calculator"))
        self.assertEqual(brain.seen, ["open calculator"])
        self.assertIn("Goal complete: opened calculator", result)
        # Narration is transcript-seam only.
        self.assertTrue(any(
            "Goal complete: opened calculator" in text
            for _role, text in session.transcript))


class AttachHelperTests(unittest.TestCase):
    def setUp(self):
        bridge.disable_voice_bridge()
        bridge.set_text_brain(None)

    def tearDown(self):
        bridge.disable_voice_bridge()
        bridge.set_text_brain(None)

    def test_attach_usable_brain(self):
        brain = _FakeTextBrain("done")
        self.assertTrue(bridge.attach_text_brain_candidate(brain))
        self.assertIs(bridge.get_text_brain(), brain)

    def test_attach_none_and_brainless_refused(self):
        brain = _FakeTextBrain("done")
        bridge.set_text_brain(brain)
        self.assertFalse(bridge.attach_text_brain_candidate(None))
        self.assertFalse(bridge.attach_text_brain_candidate(object()))
        self.assertIs(bridge.get_text_brain(), brain)

    def test_attach_voice_brain_refused(self):
        # GAPS-22 (B-V3): voice-ness is the engine attribute now,
        # never the class name — the fake carries Engine.GEMINI.
        voice_like = type(
            "GeminiLiveSession", (),
            {"engine": Engine.GEMINI,
             "run_goal": lambda self, goal: "voice"})()
        self.assertFalse(bridge.attach_text_brain_candidate(voice_like))
        self.assertIsNone(bridge.get_text_brain())

    def test_hand_off_never_drives_voice_session_as_brain(self):
        session = _FakeVoiceSession()
        bridge.enable_voice_bridge()
        # Explicit self-injection: must refuse, never recurse.
        self.assertIsNone(
            _run(bridge.hand_off(session, "do a thing",
                                 text_brain=session)))


class LiveSeamTests(unittest.TestCase):
    """The real GeminiLiveSession.run_goal seam, plan-less goal."""

    def setUp(self):
        bridge.disable_voice_bridge()
        bridge.set_text_brain(None)

    def tearDown(self):
        bridge.disable_voice_bridge()
        bridge.set_text_brain(None)

    def _session(self):
        session = GeminiLiveSession()
        captured = []
        session.on_transcript = lambda role, text: captured.append(text)
        return session, captured

    def test_seam_bridges_when_toggle_on(self):
        session, _captured = self._session()
        brain = _FakeTextBrain("Goal complete: wiring1 did the thing")
        bridge.enable_voice_bridge()
        bridge.set_text_brain(brain)
        result = _run(session.run_goal("wiring1 xyzzy frobnicate nothing"))
        self.assertEqual(brain.seen, ["wiring1 xyzzy frobnicate nothing"])
        self.assertIn("Goal complete: wiring1 did the thing", result)

    def test_seam_refuses_when_toggle_off(self):
        session, _captured = self._session()
        brain = _FakeTextBrain("Goal complete: wiring1 did the thing")
        bridge.set_text_brain(brain)
        # Toggle off: the pre-bridge honest refusal, brain untouched.
        result = _run(session.run_goal("wiring1 xyzzy frobnicate nothing"))
        self.assertEqual(brain.seen, [])
        self.assertIn("can't run a free-form goal loop", result)


class GatePassthroughTests(unittest.TestCase):
    def setUp(self):
        bridge.disable_voice_bridge()
        bridge.set_text_brain(None)

    def tearDown(self):
        bridge.disable_voice_bridge()
        bridge.set_text_brain(None)

    def test_gate_refusal_passes_through_unchanged(self):
        session = _FakeVoiceSession()
        gated = _GatedBrain()
        bridge.enable_voice_bridge()
        bridge.set_text_brain(gated)
        result = _run(bridge.hand_off(session, "delete my notes"))
        self.assertIn("needs your approval first", result)
        self.assertEqual(gated.performed, [])

    def test_gate_approval_is_brains_decision_not_bridge(self):
        session = _FakeVoiceSession()
        gated = _GatedBrain()
        gated.approved = True
        bridge.enable_voice_bridge()
        bridge.set_text_brain(gated)
        result = _run(bridge.hand_off(session, "delete my notes"))
        self.assertIn("Done:", result)
        self.assertEqual(gated.performed, ["delete my notes"])


if __name__ == "__main__":
    unittest.main()
