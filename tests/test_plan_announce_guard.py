"""Receive loop: a raising plan-announcement consumer must be surfaced,
not swallowed.

Field-check fix #20 (F2 follow-up, the last fix #17 carry-over): the
plan-announcement ``on_transcript`` call in ``_run_tool_batch`` sat
inside the method's try/except pass ("bookkeeping never breaks a
batch"), so a raising announcement consumer was eaten silently — no
log line, no loss note — while the batch continued. The raise also
aborted the announcement loop, so later announcements in the same
call never got their attempt.

The fix wraps just the announcement call, in fix #17's shape: the
failure is surfaced with a loss note and the loop continues, so a
later announcement still gets its attempt. Two differences from
fix #17/#19, both forced by what is actually lost here (measured
in the repro, not assumed): the batch completes and the model
result is still augmented either way — the loss is the transcript
lines only — and the announcement text has no other user-visible
landing (the model result is not user-visible, the planner state
is internal), so the loss note carries the text itself and the
feed keeps the record. One note per failed announcement:
announcements are sparse (one per completed step), not a stream,
so there is no flood to bound.

Harness: the same shape as test_transcript_chunk_guard.py — the
real core.live_client module, a recorder _log_line, a fake Live
session recording tool responses, and a temp plan file (the three
_PLANS_PATH bindings patched, test_routerfinish shape) so the
planner really auto-completes steps. google.genai is absent under
plain python3, so recv_mod.types gets the test_phase4 stand-in.
No test writes to data/.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "voice_assistant" not in sys.modules:
    _pkg = types.ModuleType("voice_assistant")
    _pkg.__path__ = [str(ROOT)]
    sys.modules["voice_assistant"] = _pkg
if "voice_assistant.core" not in sys.modules:
    _core = types.ModuleType("voice_assistant.core")
    _core.__path__ = [str(ROOT / "core")]
    sys.modules["voice_assistant.core"] = _core

from voice_assistant.core.voice import live_client  # noqa: E402
from voice_assistant.core.voice import live_client_receive as recv_mod  # noqa: E402
from voice_assistant.core.brain import planner  # noqa: E402
from voice_assistant.core.brain import planner_checkpoint  # noqa: E402
from voice_assistant.core.brain import planner_plans  # noqa: E402


class _FakeLiveSession:
    """Stand-in for the Live session: records tool responses."""

    def __init__(self):
        self.delivered = []

    async def send_tool_response(self, function_responses=None):
        self.delivered.extend(function_responses or [])


class PlanAnnounceGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="planannounce_"))
        self._orig_plans = planner._PLANS_PATH
        self._plans_path = self._tmp / "plans.json"
        planner._PLANS_PATH = self._plans_path
        planner_plans._PLANS_PATH = self._plans_path
        planner_checkpoint._PLANS_PATH = self._plans_path
        self.session = live_client.GeminiLiveSession()
        self.transcripts: list[tuple[str, str]] = []
        self.session.on_transcript = lambda role, text: (
            self.transcripts.append((role, text)))
        self.session.on_audio_data = None
        self.session.on_transcript_chunk = None
        self.session.on_tool_call = None
        self.session._cancelled_call_ids = set()
        self.session._running = True
        self.session.is_connected = True
        self.fake = _FakeLiveSession()
        self.session._session = self.fake
        self.session._execute_tool_sync = lambda fc: (
            {"status": "success", "result": "ORB_MAPPING scale=1.1"}
            if fc.name == "feel_orb_map"
            else {"status": "success", "result": "2026-11-17"})
        self.log_lines: list[str] = []
        self._real_log_line = recv_mod._log_line
        recv_mod._log_line = self.log_lines.append
        self._real_types = recv_mod.types
        recv_mod.types = SimpleNamespace(
            FunctionResponse=lambda **kw: SimpleNamespace(**kw))

    def tearDown(self) -> None:
        planner._PLANS_PATH = self._orig_plans
        planner_plans._PLANS_PATH = self._orig_plans
        planner_checkpoint._PLANS_PATH = self._orig_plans
        recv_mod._log_line = self._real_log_line
        recv_mod.types = self._real_types

    def _write_plan(self, steps):
        plan = [{"id": "f2p1", "steps": steps}]
        self._plans_path.write_text(json.dumps(plan), encoding="utf-8")

    def _run_batch(self, batch):
        asyncio.run(self.session._run_tool_batch(batch))

    def _delivered(self):
        return {fr.name: fr.response["result"]
                for fr in self.fake.delivered}

    def _announce_notes(self):
        return [line for line in self.log_lines
                if "could not be shown in the chat" in line
                and "plan announcement" in line]

    def _boom(self, role, text):
        raise RuntimeError("announcement consumer broke")

    def test_batch_survives_raising_announcement_consumer(self):
        # Field-check fix #20: a raising announcement consumer
        # must neither kill the batch nor lose the results — both
        # calls deliver, and the model result still carries the
        # announcement text (the augmentation happens before the
        # transcript loop, so the model never loses it).
        self._write_plan([{"tool": "feel_orb_map", "status": "pending",
                           "text": "map the orb"}])
        self.session.on_transcript = self._boom
        self._run_batch([
            SimpleNamespace(name="feel_orb_map",
                            args={"level": 0.5}, id="call-1"),
            SimpleNamespace(name="get_current_time_date",
                            args={}, id="call-2"),
        ])
        delivered = self._delivered()
        self.assertEqual(set(delivered),
                         {"feel_orb_map", "get_current_time_date"})
        self.assertIn("auto-completed",
                      str(delivered["feel_orb_map"].get("result", "")))
        self.assertEqual(self.transcripts, [])

    def test_raising_announcement_loss_is_surfaced_with_text(self):
        # Field-check fix #20: the lost announcement is surfaced,
        # not swallowed — exactly one loss note naming the failure,
        # and the note carries the announcement text itself, since
        # the transcript was its only user-visible landing.
        self._write_plan([{"tool": "feel_orb_map", "status": "pending",
                           "text": "map the orb"}])
        self.session.on_transcript = self._boom
        self._run_batch([
            SimpleNamespace(name="feel_orb_map",
                            args={"level": 0.5}, id="call-1"),
        ])
        notes = self._announce_notes()
        self.assertEqual(len(notes), 1)
        self.assertIn("RuntimeError: announcement consumer broke",
                      notes[0])
        self.assertIn("Plan f2p1 step 1 auto-completed", notes[0])
        self.assertIn("map the orb", notes[0])

    def test_healthy_consumer_receives_announcement(self):
        # Control: with a healthy consumer the announcement
        # reaches the transcript, no loss note appears, and the
        # delivered result is augmented with the same text.
        self._write_plan([{"tool": "feel_orb_map", "status": "pending",
                           "text": "map the orb"}])
        self._run_batch([
            SimpleNamespace(name="feel_orb_map",
                            args={"level": 0.5}, id="call-1"),
        ])
        self.assertEqual(len(self.transcripts), 1)
        self.assertIn("auto-completed", self.transcripts[0][1])
        self.assertIn("map the orb", self.transcripts[0][1])
        self.assertEqual(self._announce_notes(), [])
        delivered = self._delivered()
        self.assertIn("auto-completed",
                      str(delivered["feel_orb_map"].get("result", "")))

    def test_flaky_consumer_second_announcement_still_attempted(self):
        # Field-check fix #20: the guard sits inside the loop, so
        # a failure on the first announcement does not abort the
        # rest — one call completing two steps attempts both
        # announcements; the second lands, and exactly one note
        # surfaces the first.
        self._write_plan([
            {"tool": "feel_orb_map", "status": "pending",
             "text": "map the orb"},
            {"tool": "feel_orb_map", "status": "pending",
             "text": "dim the orb"},
        ])
        calls = {"n": 0}

        def _flaky(role, text):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("announcement consumer broke")
            self.transcripts.append((role, text))

        self.session.on_transcript = _flaky
        self._run_batch([
            SimpleNamespace(name="feel_orb_map",
                            args={"level": 0.5}, id="call-1"),
        ])
        # The second announcement lands — and the loss note rides
        # the recovered consumer as a chat line (the _loss_note
        # feed-first/chat-second path, fix #17's shape), so two
        # transcript lines land: the note, then the announcement.
        self.assertEqual(len(self.transcripts), 2)
        self.assertTrue(any("dim the orb" in text
                            for _r, text in self.transcripts))
        self.assertTrue(any("could not be shown in the chat" in text
                            for _r, text in self.transcripts))
        self.assertEqual(len(self._announce_notes()), 1)
        self.assertIn("map the orb", self._announce_notes()[0])

    def test_two_calls_note_one_announcement_each(self):
        # Field-check fix #20: the note is per failed announcement,
        # not once per batch — two calls completing two steps with
        # a broken consumer surface two notes, one carrying each
        # announcement's text.
        self._write_plan([
            {"tool": "feel_orb_map", "status": "pending",
             "text": "map the orb"},
            {"tool": "get_current_time_date", "status": "pending",
             "text": "check the date"},
        ])
        self.session.on_transcript = self._boom
        self._run_batch([
            SimpleNamespace(name="feel_orb_map",
                            args={"level": 0.5}, id="call-1"),
            SimpleNamespace(name="get_current_time_date",
                            args={}, id="call-2"),
        ])
        notes = self._announce_notes()
        self.assertEqual(len(notes), 2)
        self.assertTrue(any("map the orb" in line for line in notes))
        self.assertTrue(any("check the date" in line
                            for line in notes))
        self.assertEqual(set(self._delivered()),
                         {"feel_orb_map", "get_current_time_date"})

    def test_no_announcement_no_note(self):
        # Control: with no pending step there is no announcement
        # and no note — the fix adds nothing when there is
        # nothing to announce.
        self._write_plan([])
        self.session.on_transcript = self._boom
        self._run_batch([
            SimpleNamespace(name="feel_orb_map",
                            args={"level": 0.5}, id="call-1"),
        ])
        self.assertEqual(self._announce_notes(), [])
        self.assertEqual(set(self._delivered()), {"feel_orb_map"})


if __name__ == "__main__":
    unittest.main()
