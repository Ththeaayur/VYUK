"""Wave 1 bridge tests (stdlib unittest, no GUI, no network, no model).

Covers core/voice_text_bridge.py and its live_client integration
shape: flag default OFF, honest refusal when OFF or brainless,
handoff + narration when ON with an injected brain, verbatim
report (a halted report stays halted), and no brain creation.
"""

import asyncio
import unittest

from core.voice import voice_text_bridge as bridge


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


class _BrokenBrain:
    async def run_goal(self, goal_text):
        raise RuntimeError("brain exploded")


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class BridgeFlagTests(unittest.TestCase):
    def setUp(self):
        bridge.disable_voice_bridge()
        bridge.set_text_brain(None)

    def tearDown(self):
        bridge.disable_voice_bridge()
        bridge.set_text_brain(None)

    def test_default_off(self):
        self.assertFalse(bridge.is_voice_bridge_enabled())

    def test_off_returns_none_so_caller_refuses(self):
        session = _FakeVoiceSession()
        brain = _FakeTextBrain("Goal complete: done")
        bridge.set_text_brain(brain)
        # Still OFF: hand_off must return None, brain never called.
        self.assertIsNone(_run(bridge.hand_off(session, "do a thing")))
        self.assertEqual(brain.seen, [])
        self.assertEqual(session.transcript, [])

    def test_on_without_brain_returns_none(self):
        session = _FakeVoiceSession()
        bridge.enable_voice_bridge()
        self.assertIsNone(_run(bridge.hand_off(session, "do a thing")))

    def test_empty_goal_is_honest(self):
        session = _FakeVoiceSession()
        bridge.enable_voice_bridge()
        bridge.set_text_brain(_FakeTextBrain("Goal complete: done"))
        result = _run(bridge.hand_off(session, "   "))
        self.assertIn("no goal text", result)

    def test_handoff_narrates_verbatim_report(self):
        session = _FakeVoiceSession()
        brain = _FakeTextBrain("Goal complete: opened calculator")
        bridge.enable_voice_bridge()
        bridge.set_text_brain(brain)
        result = _run(bridge.hand_off(session, "open calculator"))
        self.assertIn("opened calculator", result)
        self.assertEqual(brain.seen, ["open calculator"])
        # Narrated through the transcript seam, assistant role.
        self.assertEqual(len(session.transcript), 1)
        self.assertEqual(session.transcript[0][0], "assistant")
        self.assertIn("opened calculator", session.transcript[0][1])

    def test_halted_report_stays_halted(self):
        session = _FakeVoiceSession()
        brain = _FakeTextBrain("Goal halted: the failsafe is tripped")
        bridge.enable_voice_bridge()
        bridge.set_text_brain(brain)
        result = _run(bridge.hand_off(session, "do a thing"))
        self.assertIn("Goal halted:", result)
        self.assertNotIn("Goal complete:", result)

    def test_brain_without_run_goal_is_honest(self):
        session = _FakeVoiceSession()
        bridge.enable_voice_bridge()
        result = _run(bridge.hand_off(session, "do a thing",
                                      text_brain=object()))
        self.assertIn("no run_goal", result)

    def test_brain_failure_is_reported_not_faked(self):
        session = _FakeVoiceSession()
        bridge.enable_voice_bridge()
        result = _run(bridge.hand_off(session, "do a thing",
                                      text_brain=_BrokenBrain()))
        self.assertIn("did not complete", result)
        self.assertNotIn("Goal complete:", result)

    def test_narration_failure_does_not_rewrite_report(self):
        class _BrokenSession:
            def on_transcript(self, role, text):
                raise RuntimeError("transcript down")
        brain = _FakeTextBrain("Goal complete: done")
        bridge.enable_voice_bridge()
        result = _run(bridge.hand_off(_BrokenSession(), "do a thing",
                                      text_brain=brain))
        self.assertIn("Goal complete: done", result)
        self.assertFalse(bridge.narrate_report(_BrokenSession(), "x"))


if __name__ == "__main__":
    unittest.main()
