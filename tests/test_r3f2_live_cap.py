"""Round-3 fix #2 (#55): the LIVE tool loop's finite hard round cap.

What this suite proves (simulated sessions only — no live Gemini, no
real audio, no real Qt):
(a) the cap is 8 rounds per model turn, matching the cloud text loop's
    MAX_TOOL_ROUNDS = 8 (config.live_tool_rounds / LIVE_TOOL_ROUNDS);
(b) 12 sequential tool batches in one turn execute exactly 8 — the
    rest execute nothing and deliver nothing — and the user is told
    once, on screen (on_transcript chat line) and in the log;
(c) further refused batches in the same turn log only, so the chat
    is never flooded with one announcement per refused batch;
(d) a user 'continue' starts a NEW capped loop: after the
    turn-boundary reset a fresh turn gets its own 8 rounds, and the
    9th batch of that turn is refused and announced again;
(e) a batch with no dispatchable call (all calls pre-cancelled)
    consumes no round;
(f) the cap holds on the production path too — the tool worker loop
    draining the real queue — not just on direct _run_tool_batch calls;
(g) the cap value reads the config seam and falls back to 8 on a
    missing, non-positive, or non-numeric value.

Harness: the test_phase4 shape — a bare GeminiLiveSession built with
object.__new__ (no client construction), the google-genai types
stand-in on both bindings (lab python3 has no genai package; the
delivery tail builds FunctionResponse objects from it), a temp
receipt store, and a stubbed executor + delivery so no test writes
to data/. The section-A repro shape (12 get_current_time_date
batches) is the fidelity anchor; the real-registry half was proven
by the repro on a post-fix scratch (REPRO-EXIT 0).
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import tool_receipts  # noqa: E402
from core.config import config  # noqa: E402
from core.voice import live_client  # noqa: E402
from core.voice import live_client_receive as live_client_receive_mod  # noqa: E402
from core.voice.cloud_text_helpers import _max_tool_rounds as _cloud_max  # noqa: E402
from core.voice.live_client import GeminiLiveSession  # noqa: E402


class LiveCapHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tool_receipts.install_store(Path(self._tmp.name) / "r.jsonl")
        self.addCleanup(tool_receipts.reset_default)
        # Lab has no google-genai package, so live_client.types is
        # None here; the delivery tail builds FunctionResponse
        # objects from it. Stub only the constructor shape — the
        # filtering logic under test is production code, not the
        # stub. (test_phase4 shape; patch both bindings.)
        self._orig_types = live_client.types
        _fake_types = SimpleNamespace(
            FunctionResponse=lambda **kw: SimpleNamespace(**kw))
        live_client.types = _fake_types
        self.addCleanup(setattr, live_client, "types",
                        self._orig_types)
        self._orig_types_recv = live_client_receive_mod.types
        live_client_receive_mod.types = _fake_types
        self.addCleanup(setattr, live_client_receive_mod, "types",
                        self._orig_types_recv)
        # The cap announcement goes through _loss_note (log +
        # chat line); record the log half instead of writing
        # data/activity.log.
        self.log_lines: list[str] = []
        self._orig_log = live_client_receive_mod._log_line
        live_client_receive_mod._log_line = self.log_lines.append
        self.addCleanup(setattr, live_client_receive_mod, "_log_line",
                        self._orig_log)

    def _session(self):
        session = object.__new__(GeminiLiveSession)
        session._cancelled_call_ids = set()
        session._running = True
        session._live_tool_rounds = 0
        session._live_tool_cap_announced = False
        self.executed: list[str] = []
        self.delivered: list = []
        self.transcripts: list[tuple[str, str]] = []

        def execute(fc):
            self.executed.append(fc.id)
            return {"status": "success", "result": "ok-" + fc.id}

        session._execute_tool_sync = execute
        session.on_tool_call = lambda n, a, r: None
        session.on_transcript = lambda role, text: (
            self.transcripts.append((role, text)))

        async def _deliver(responses):
            self.delivered.extend(responses)

        session._deliver_tool_responses = _deliver
        return session

    def _fc(self, i):
        return SimpleNamespace(
            id=f"fc-{i}", name="get_current_time_date", args={})

    def _run_batches(self, session, count, start=0):
        async def main():
            for i in range(start, start + count):
                await session._run_tool_batch([self._fc(i)])

        asyncio.run(main())

    def _cap_notes(self):
        return [t for (_r, t) in self.transcripts
                if "tool round" in t.lower()]


class TestLiveCapValue(LiveCapHarness):
    def test_cap_is_8_matching_cloud_text_loop(self):
        self.assertEqual(live_client_receive_mod.LIVE_MAX_TOOL_ROUNDS, 8)
        self.assertEqual(live_client_receive_mod._max_live_tool_rounds(), 8)
        self.assertEqual(getattr(config, "live_tool_rounds", None), 8)
        self.assertEqual(_cloud_max(), 8)

    def test_cap_reads_config_and_falls_back(self):
        mod = live_client_receive_mod
        with mock.patch.object(mod, "config",
                               SimpleNamespace(live_tool_rounds=3)):
            self.assertEqual(mod._max_live_tool_rounds(), 3)
        with mock.patch.object(mod, "config",
                               SimpleNamespace(live_tool_rounds=0)):
            self.assertEqual(mod._max_live_tool_rounds(), 8)
        with mock.patch.object(mod, "config",
                               SimpleNamespace(live_tool_rounds="bad")):
            self.assertEqual(mod._max_live_tool_rounds(), 8)


class TestLiveCapEnforced(LiveCapHarness):
    def test_twelve_batches_execute_eight_and_announce_once(self):
        session = self._session()
        self._run_batches(session, 12)
        self.assertEqual(len(self.executed), 8)
        self.assertEqual(len(self.delivered), 8)
        self.assertEqual(len(self._cap_notes()), 1)
        note = self._cap_notes()[0]
        self.assertIn("8 tool rounds", note)
        self.assertIn("didn't run", note)
        self.assertIn("continue", note.lower())
        self.assertTrue(
            any("tool-round cap" in line or "tool rounds" in line
                for line in self.log_lines))

    def test_refused_batches_log_only_no_second_announcement(self):
        session = self._session()
        self._run_batches(session, 9)
        self.assertEqual(len(self.executed), 8)
        self.assertEqual(len(self._cap_notes()), 1)
        log_before = len(self.log_lines)
        self._run_batches(session, 3, start=100)
        self.assertEqual(len(self.executed), 8)
        self.assertEqual(len(self._cap_notes()), 1)
        self.assertGreater(len(self.log_lines), log_before)

    def test_all_cancelled_batch_consumes_no_round(self):
        session = self._session()
        session._cancelled_call_ids = {"fc-0"}
        self._run_batches(session, 1)
        self.assertEqual(self.executed, [])
        self._run_batches(session, 8, start=1)
        self.assertEqual(len(self.executed), 8)
        self.assertEqual(self._cap_notes(), [])

    def test_continue_starts_a_new_capped_loop(self):
        session = self._session()
        self._run_batches(session, 12)
        self.assertEqual(len(self.executed), 8)
        self.assertEqual(len(self._cap_notes()), 1)
        # The turn boundary: the receive loop calls this when a
        # model turn completes. A user 'continue' is a new turn.
        session._reset_live_tool_rounds()
        self.executed.clear()
        self.delivered.clear()
        self._run_batches(session, 8, start=100)
        self.assertEqual(len(self.executed), 8)
        self.assertEqual(len(self._cap_notes()), 1)
        self._run_batches(session, 1, start=108)
        self.assertEqual(len(self.executed), 8)
        self.assertEqual(len(self._cap_notes()), 2)

    def test_worker_loop_enforces_the_cap(self):
        session = self._session()

        async def main():
            session._tool_queue = asyncio.Queue()
            worker = asyncio.create_task(session._tool_worker_loop())
            try:
                for i in range(12):
                    session._tool_queue.put_nowait([self._fc(i)])
                loop = asyncio.get_running_loop()
                start = loop.time()
                # Bounded wait on MEASURED elapsed time: the
                # stubbed worker drains in milliseconds, but a
                # stall must end the wait instead of hanging it.
                while ((len(self.executed) < 8
                        or not session._tool_queue.empty())
                        and loop.time() - start < 5.0):
                    await asyncio.sleep(0.05)
                self.assertEqual(len(self.executed), 8)
                self.assertTrue(session._tool_queue.empty())
            finally:
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass

        asyncio.run(main())
        self.assertEqual(len(self.delivered), 8)
        self.assertEqual(len(self._cap_notes()), 1)
