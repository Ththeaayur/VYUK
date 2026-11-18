"""Fourth-audit Phase 4: cancellation/standby stress (C04).

The audit's claim: a cancelled warm standby can retain ongoing
executor work — cancelling the asyncio task cannot forcibly
terminate work already running in its worker thread, so a
discarded standby could keep CPU/RAM busy after the cloud won
or the user disconnected. The remediation: make model-load
requests cancellation-aware where supported, serialize expensive
loads, retain ownership until worker completion, and account for
memory pressure.

This suite pins the six stress cases from the Phase 4 brief, each
driving the REAL cancel path (asyncio task cancellation, the real
coalescer, the real _start_cloud discard, the real batch loops):

1. Cancel mid-tool-execution (Groq + local): CancelledError
   surfaces, no further dispatch happens, and a late worker
   result is discarded, never applied (Phase 2 executor rule).
2. Cancel during repair/deliberation (local repair, Groq
   deliberation guard): budgets stop cleanly, no repair retry
   is injected after cancel, and the Phase 3 escalation record
   survives the cancellation (it is not silently swallowed).
3. Cancel during streaming/model output: there is NO true
   streaming path in this tree (local _llama_chat is blocking
   executor-only, Groq/Ollama use stream=False) — so the
   lab-drivable analogue is cancel during a blocking model
   call: the turn dies via CancelledError and no partial
   output is presented as a complete answer. A live-model
   stream cancel is field-owed, recorded as such, never faked.
4. Standby then resume: standby parks state without executing;
   resume is explicit-user-action only (ledger reports
   interrupted tasks, never auto-runs them; _fallback_warranted
   is False after stop).
5. Double-cancel and cancel-after-complete: safe no-ops with
   honest reports, never errors, never re-execution.
6. Receipts/journal correctness under cancellation: the Phase 2
   receipt vocabulary gains "cancelled" with a dispatched flag
   — cancelled-before-dispatch is safe to re-run, cancelled-
   during-dispatch blocks like unknown; the live batch records
   a cancelled receipt for every pre-dispatch skip.

No real model, no network, no funnel tools. Blocking fakes use
threads + events; every test restores the modules it patches.
"""

import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.voice import cloud_text_client  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.voice import cloud_text_turn as cloud_text_turn_mod  # B39 INTENTIONAL: cloud split — turn methods resolve `tools` / `_max_tool_rounds` in turn-mixin globals; tests patch both bindings
from core.voice import live_client  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.voice import live_client_receive as live_client_receive_mod  # B47 INTENTIONAL: receive mixin binding for dual patches
from core.voice import live_client_resume as live_client_resume_mod  # B47 INTENTIONAL: resume module binding for dual patches
from core.voice import local_client  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core import standby_loads
from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.brain import task_ledger as tl  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core import tool_receipts
from core.brain.brain import BrainSession  # Phase 3 B2h: brain hub moved to core/brain/brain.py INTENTIONAL: brain hub moved to core/brain/brain.py
from core.voice.cloud_text_client import GroqSession  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.groq_action_journal import GroqActionJournal
from core.voice.live_client import GeminiLiveSession  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.voice.local_client import LocalLiveSession  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.standby_loads import StandbyLoadCoalescer
from core.brain.task_coordinator import StepDef  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.tool_receipts import ReceiptStore


# ---------------------------------------------------------------- #
# Shared fakes
# ---------------------------------------------------------------- #

def _decl(name):
    return {"name": name, "description": "fake " + name,
            "parameters": {"type": "object", "properties": {}}}


class BlockingBus:
    """Fake tool bus with one blocking tool (slow_tool)."""

    def __init__(self):
        self.calls = []
        self.tool_started = threading.Event()
        self.tool_release = threading.Event()

    def get_declarations(self):
        return [_decl(n) for n in ("tool_a", "tool_b", "slow_tool")]

    def execute(self, name, args):
        self.calls.append((name, dict(args)))
        if name == "slow_tool":
            self.tool_started.set()
            self.tool_release.wait(10)
            return {"status": "success", "result": "did-slow_tool"}
        if name == "tool_b":
            return {"status": "refused", "kind": "validation",
                    "validation_error": True,
                    "message": "bad args for tool_b"}
        return {"status": "success", "result": "did-" + name}

    def count(self, name):
        return sum(1 for n, _ in self.calls if n == name)


def _groq_tool_response(calls):
    return (200, {"choices": [{"message": {
        "content": "",
        "tool_calls": [
            {"id": cid,
             "function": {"name": name, "arguments": json.dumps(args)}}
            for cid, name, args in calls]}}]})


def _groq_text_response(text):
    return (200, {"choices": [{"message": {
        "content": text, "tool_calls": []}}]})


def _tool_json(name, args=None):
    return json.dumps({"tool": name, "arguments": args or {}})


def _batch_json(calls):
    return json.dumps([{"tool": n, "arguments": a} for n, a in calls])


# ---------------------------------------------------------------- #
# 6. Receipt "cancelled" state
# ---------------------------------------------------------------- #

class TestCancelledReceipts(unittest.TestCase):
    def test_cancelled_is_a_valid_outcome_status(self):
        store = ReceiptStore(path=Path(tempfile.mkdtemp()) / "r.jsonl")
        attempt = store.record_attempt(tool="t", args={"n": 1})
        out = store.record_outcome(attempt["receipt_id"],
                                   status="cancelled",
                                   summary="cancelled before dispatch",
                                   dispatched=False)
        self.assertEqual(out["status"], "cancelled")
        self.assertFalse(out["dispatched"])
        with self.assertRaises(ValueError):
            store.record_outcome(attempt["receipt_id"],
                                 status="vanished")

    def test_cancelled_before_dispatch_does_not_block_rerun(self):
        """Cancelled + never dispatched: no effect happened, so
        the guard ignores it (unlike unknown)."""
        store = ReceiptStore(path=Path(tempfile.mkdtemp()) / "r.jsonl")
        attempt = store.record_attempt(tool="t", args={"n": 1})
        store.record_outcome(attempt["receipt_id"], status="cancelled",
                             dispatched=False)
        self.assertIsNone(store.find_unknown(tool="t", args={"n": 1}))

    def test_cancelled_during_dispatch_blocks_like_unknown(self):
        """Cancelled + dispatched: the call may have run, so the
        guard refuses re-execution until verified — exactly
        like an unknown outcome."""
        store = ReceiptStore(path=Path(tempfile.mkdtemp()) / "r.jsonl")
        attempt = store.record_attempt(tool="t", args={"n": 1})
        store.record_outcome(attempt["receipt_id"], status="cancelled",
                             dispatched=True)
        found = store.find_unknown(tool="t", args={"n": 1})
        self.assertIsNotNone(found)
        self.assertEqual(found["status"], "cancelled")

    def test_cancelled_record_survives_reload(self):
        """Outcome records carry dispatched across a reload;
        the guard's answer is the same before and after."""
        tmp = Path(tempfile.mkdtemp()) / "r.jsonl"
        store = ReceiptStore(path=tmp)
        attempt = store.record_attempt(tool="t", args={"n": 1})
        store.record_outcome(attempt["receipt_id"], status="cancelled",
                             dispatched=False)
        reloaded = ReceiptStore(path=tmp)
        self.assertIsNone(reloaded.find_unknown(tool="t", args={"n": 1}))
        attempt2 = reloaded.record_attempt(tool="u", args={})
        reloaded.record_outcome(attempt2["receipt_id"],
                                status="cancelled", dispatched=True)
        self.assertIsNotNone(
            ReceiptStore(path=tmp).find_unknown(tool="u", args={}))

    def test_try_record_cancelled_helper(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tool_receipts.install_store(Path(tmp.name) / "r.jsonl")
        self.addCleanup(tool_receipts.reset_default)
        attempt = tool_receipts.try_record_cancelled(
            tool="t", args={"n": 1}, summary="cancelled before dispatch")
        self.assertIsNotNone(attempt)
        store = tool_receipts._get_store()
        self.assertIsNone(store.find_unknown(tool="t", args={"n": 1}))
        records = (Path(tmp.name) / "r.jsonl").read_text(
            encoding="utf-8").strip().splitlines()
        outcomes = [json.loads(r) for r in records
                    if json.loads(r)["type"] == "outcome"]
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["status"], "cancelled")
        self.assertFalse(outcomes[0]["dispatched"])

    def test_try_record_cancelled_dispatched_blocks_rerun(self):
        # Field-check fix #2 (T7 #17): a call cancelled DURING
        # execution ran, so its receipt carries dispatched=True
        # and the guard treats the outcome as unknown — a later
        # re-run is refused until verified.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tool_receipts.install_store(Path(tmp.name) / "r.jsonl")
        self.addCleanup(tool_receipts.reset_default)
        attempt = tool_receipts.try_record_cancelled(
            tool="t", args={"n": 1},
            summary="cancelled during execution — result dropped "
                    "at delivery",
            dispatched=True)
        self.assertIsNotNone(attempt)
        store = tool_receipts._get_store()
        self.assertIsNotNone(store.find_unknown(tool="t", args={"n": 1}))
        records = (Path(tmp.name) / "r.jsonl").read_text(
            encoding="utf-8").strip().splitlines()
        outcomes = [json.loads(r) for r in records
                    if json.loads(r)["type"] == "outcome"]
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["status"], "cancelled")
        self.assertTrue(outcomes[0]["dispatched"])

    def test_resolved_outcomes_still_clear_the_guard(self):
        """Regression guard for the Phase 2 vocabulary: adding
        'cancelled' must not change succeeded/failed/refused
        clearing the unknown set."""
        store = ReceiptStore(path=Path(tempfile.mkdtemp()) / "r.jsonl")
        for status in ("succeeded", "failed", "refused"):
            attempt = store.record_attempt(tool="t", args={"s": status})
            store.record_outcome(attempt["receipt_id"], status=status)
            self.assertIsNone(
                store.find_unknown(tool="t", args={"s": status}))


# ---------------------------------------------------------------- #
# 4. Standby abandonment (C04 core) + 5. discard paths
# ---------------------------------------------------------------- #

class TestStandbyAbandonment(unittest.TestCase):
    def test_audit_regression_cloud_wins_cancel_standby(self):
        """The audit's regression test, adapted to the coalescer:
        a blocking fake preload, a waiter cancelled (cloud won),
        then abandon: no second preload overlaps, and resources
        are eventually released (registry empty, semaphore
        free, release called with the session)."""
        async def main():
            coalescer = StandbyLoadCoalescer()
            loads = []
            releases = []
            started = threading.Event()
            release_block = threading.Event()
            fake_session = SimpleNamespace(_llama=object(),
                                           _whisper=object())

            def blocking_preload():
                loads.append("load")
                started.set()
                release_block.wait(10)
                return fake_session

            async def build():
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None,
                                                  blocking_preload)

            task = coalescer.prepare("model-x", build)
            await asyncio.to_thread(started.wait, 5)
            self.assertEqual(loads, ["load"])
            # A waiter arrives and is cancelled (cloud won):
            # the load itself must keep running, shielded.
            waiter = asyncio.create_task(coalescer.await_shared(task))
            await asyncio.sleep(0)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            self.assertFalse(task.done())
            self.assertEqual(coalescer.in_flight(), ["model-x"])
            # A second prepare overlaps nothing: same task.
            task2 = coalescer.prepare("model-x", build)
            self.assertIs(task2, task)
            self.assertEqual(loads, ["load"])
            # Abandon: release fires on completion, not before.
            coalescer.abandon("model-x", release=releases.append)
            self.assertTrue(coalescer.abandoned("model-x"))
            self.assertEqual(releases, [])
            release_block.set()
            await task
            self.assertEqual(releases, [fake_session])
            self.assertEqual(coalescer.in_flight(), [])
            self.assertTrue(coalescer.semaphore_available())
            self.assertFalse(coalescer.abandoned("model-x"))

        asyncio.run(main())

    def test_new_prepare_revokes_abandonment(self):
        """A new waiter owns the load again: the release must
        NOT fire under a live owner."""
        async def main():
            coalescer = StandbyLoadCoalescer()
            releases = []
            started = threading.Event()
            release_block = threading.Event()
            fake_session = SimpleNamespace()

            def blocking_preload():
                started.set()
                release_block.wait(10)
                return fake_session

            async def build():
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None,
                                                  blocking_preload)

            task = coalescer.prepare("model-y", build)
            await asyncio.to_thread(started.wait, 5)
            coalescer.abandon("model-y", release=releases.append)
            self.assertTrue(coalescer.abandoned("model-y"))
            # New prepare: same task, mark revoked.
            self.assertIs(coalescer.prepare("model-y", build), task)
            self.assertFalse(coalescer.abandoned("model-y"))
            release_block.set()
            self.assertIs(await task, fake_session)
            self.assertEqual(releases, [])

        asyncio.run(main())

    def test_abandon_with_no_in_flight_load_is_a_noop(self):
        async def main():
            coalescer = StandbyLoadCoalescer()
            releases = []
            coalescer.abandon("nope", release=releases.append)
            self.assertFalse(coalescer.abandoned("nope"))
            self.assertEqual(releases, [])

            async def quick():
                return SimpleNamespace()

            task = coalescer.prepare("fast", quick)
            await task
            coalescer.abandon("fast", release=releases.append)
            self.assertEqual(releases, [])
            self.assertFalse(coalescer.abandoned("fast"))

        asyncio.run(main())


class BrainDiscardHarness(unittest.TestCase):
    def setUp(self):
        self._fresh = StandbyLoadCoalescer()
        self._orig_coalescer = standby_loads.default_coalescer
        standby_loads.default_coalescer = self._fresh
        self.addCleanup(setattr, standby_loads, "default_coalescer",
                        self._orig_coalescer)
        from core.brain import brain as brain_mod  # Phase 3 B2h: brain hub moved to core/brain/brain.py INTENTIONAL: binds the hub submodule via the new package
        self._orig_delay = brain_mod.CLOUD_STANDBY_DELAY_S
        brain_mod.CLOUD_STANDBY_DELAY_S = 0
        self.addCleanup(setattr, brain_mod, "CLOUD_STANDBY_DELAY_S",
                        self._orig_delay)
        # Save the raw staticmethod descriptor: restoring the
        # class-accessed function as a plain attribute would
        # bind self on later calls and break the real method.
        self._orig_mid = BrainSession.__dict__["_standby_model_id"]
        BrainSession._standby_model_id = staticmethod(
            lambda: "test-model")
        self.addCleanup(setattr, BrainSession, "_standby_model_id",
                        self._orig_mid)

    def _brain(self, fake_session, preload):
        session = object.__new__(BrainSession)
        session._saw_connected = False
        session._stop_requested = False
        session._inner = None
        session._standby_inner = "sentinel"
        session._build_local_inner = lambda: fake_session
        session._preload_local_session = preload
        return session


class TestBrainDiscard(BrainDiscardHarness):
    def test_cloud_win_discards_standby_and_releases_on_completion(self):
        """Full _start_cloud path: cloud wins while the standby
        preload blocks; the standby is abandoned (never
        installed), and when the preload finishes its session
        is released (model refs dropped), the registry drains
        and the semaphore frees. No second preload runs."""
        builds = []
        preload_started = threading.Event()
        preload_release = threading.Event()
        fake_session = SimpleNamespace(_llama=object(),
                                       _whisper=object())
        # GAPS-22 (B-V6): the standby drops its own models through
        # release_model() — the fake stands in for a never-started
        # local session, so it implements the same drop.
        def _release_fake():
            fake_session._llama = None
            fake_session._whisper = None
        fake_session.release_model = _release_fake

        def preload(session):
            builds.append(session)
            preload_started.set()
            preload_release.wait(10)

        brain = self._brain(fake_session, preload)

        async def main():
            cloud_go = asyncio.Event()

            class FakeInner:
                async def start(self, audio_manager):
                    await cloud_go.wait()
                    brain._saw_connected = True

            fallback_seen = []

            async def fake_fallback(audio_manager, prepared=None):
                fallback_seen.append(prepared)

            brain._maybe_fallback_to_local = fake_fallback
            start_task = asyncio.create_task(
                brain._start_cloud(FakeInner(), None))
            await asyncio.to_thread(preload_started.wait, 5)
            # Standby is loading; the cloud has not won yet.
            self.assertEqual(builds, [fake_session])
            cloud_go.set()
            await start_task
            # Discarded, never installed; fallback got nothing.
            self.assertIsNone(brain._standby_inner)
            self.assertEqual(fallback_seen, [None])
            # Still loading: abandoned, release pending.
            self.assertTrue(self._fresh.abandoned("test-model"))
            self.assertEqual(self._fresh.in_flight(), ["test-model"])
            preload_release.set()
            await asyncio.sleep(0.2)
            # Released: model refs dropped, registry drained.
            self.assertIsNone(fake_session._llama)
            self.assertIsNone(fake_session._whisper)
            self.assertEqual(self._fresh.in_flight(), [])
            self.assertTrue(self._fresh.semaphore_available())
            self.assertFalse(self._fresh.abandoned("test-model"))
            self.assertEqual(builds, [fake_session])

        asyncio.run(main())

    def test_discard_done_standby_releases_directly(self):
        """A standby task that already completed with a session
        is released directly by _discard_standby (the coalescer
        has nothing in flight to abandon)."""
        fake = SimpleNamespace(_llama=object(), _whisper=object())
        # GAPS-22 (B-V6): see test_cloud_win_discards_standby... —
        # the fake implements the release itself.
        def _release_fake():
            fake._llama = None
            fake._whisper = None
        fake.release_model = _release_fake
        brain = self._brain(fake, lambda session: None)

        async def main():
            async def done_task():
                return fake

            task = asyncio.create_task(done_task())
            await task
            brain._discard_standby(task)
            self.assertIsNone(fake._llama)
            self.assertIsNone(fake._whisper)

        asyncio.run(main())

    def test_discard_cancelled_or_failed_standby_is_silent(self):
        brain = self._brain(SimpleNamespace(), lambda session: None)

        async def main():
            async def boom():
                raise RuntimeError("standby build blew up")

            failed = asyncio.create_task(boom())
            with self.assertRaises(RuntimeError):
                await failed
            brain._discard_standby(failed)  # no raise

            async def hang():
                await asyncio.sleep(60)

            pending = asyncio.create_task(hang())
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            brain._discard_standby(pending)  # no raise

        asyncio.run(main())

    def test_fallback_warranted_false_after_stop(self):
        """Stop blocks fallback auto-run: a stopped brain never
        claims a standby on its own."""
        brain = self._brain(SimpleNamespace(), lambda session: None)
        self.assertTrue(brain._fallback_warranted())
        brain._stop_requested = True
        self.assertFalse(brain._fallback_warranted())


# ---------------------------------------------------------------- #
# 1/2/3. Groq turn cancellation
# ---------------------------------------------------------------- #

class GroqCancelHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bus = BlockingBus()
        self._orig_tools = cloud_text_client.tools
        cloud_text_client.tools = self.bus
        self.addCleanup(setattr, cloud_text_client, "tools",
                        self._orig_tools)
        # B39 INTENTIONAL: _run_tool_call / _guard_state_answer /
        # _brain_turn resolve `tools` in turn-mixin globals now, so
        # the facade patch alone would miss them — patch both
        # bindings (B16/B33/B35 dual-patch pattern), same fake.
        self._orig_turn_tools = cloud_text_turn_mod.tools
        cloud_text_turn_mod.tools = self.bus
        self.addCleanup(setattr, cloud_text_turn_mod, "tools",
                        self._orig_turn_tools)
        self._orig_log = cloud_text_client.memory_store.log_message
        cloud_text_client.memory_store.log_message = lambda *a, **k: None
        self.addCleanup(setattr, cloud_text_client.memory_store,
                        "log_message", self._orig_log)
        self.transcripts = []

    def _session(self):
        session = GroqSession()
        session._action_journal = GroqActionJournal(
            path=Path(self._tmp.name) / "journal.json")
        session._system_prompt = "test system"

        async def _noop(*args, **kwargs):
            return None

        session._speak = _noop
        session._maybe_auto_summarize = _noop
        session.on_transcript = lambda role, text: (
            self.transcripts.append(text) if role == "assistant" else None)
        return session


class TestGroqCancel(GroqCancelHarness):
    def test_cancel_mid_tool_execution_discards_late_result(self):
        """Case 1 (Groq): cancel while the tool runs in the
        executor — CancelledError surfaces, no second model
        call happens, and the tool's late result is never
        applied (no tool message in history, no final text)."""
        session = self._session()
        calls = []
        session._chat_completion = lambda messages: (
            calls.append("completion"),
            _groq_tool_response([("call_1", "slow_tool", {})])
            if len(calls) == 1 else
            _groq_text_response("never reached"))[1]

        async def main():
            turn = asyncio.create_task(session._brain_turn("do it"))
            await asyncio.to_thread(self.bus.tool_started.wait, 5)
            turn.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await turn
            self.assertEqual(len(calls), 1)
            self.bus.tool_release.set()
            await asyncio.sleep(0.3)
            # The worker finished, but the turn consumed nothing.
            self.assertEqual(self.bus.count("slow_tool"), 1)
            roles = [m.get("role") for m in session._history]
            self.assertNotIn("tool", roles)
            self.assertEqual(self.transcripts, [])

        asyncio.run(main())

    def test_cancel_mid_batch_no_later_tool_dispatched(self):
        """Case 1 (Groq, multi-tool): unlike the local sync
        batch, the Groq loop awaits each tool via run_blocking,
        so a cancel during tool 1 is noticed before tool 2 is
        even scheduled — tool 2 never dispatches."""
        session = self._session()
        calls = []
        session._chat_completion = lambda messages: (
            calls.append("completion"),
            _groq_tool_response([("call_1", "slow_tool", {}),
                                 ("call_2", "tool_a", {})])
            if len(calls) == 1 else
            _groq_text_response("never reached"))[1]

        async def main():
            turn = asyncio.create_task(session._brain_turn("do both"))
            await asyncio.to_thread(self.bus.tool_started.wait, 5)
            turn.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await turn
            self.bus.tool_release.set()
            await asyncio.sleep(0.3)
            self.assertEqual(self.bus.count("slow_tool"), 1)
            self.assertEqual(self.bus.count("tool_a"), 0)
            self.assertEqual(self.transcripts, [])

        asyncio.run(main())

    def test_cancel_during_model_call_no_partial_answer(self):
        """Case 3 (Groq, lab analogue): no streaming path exists
        (stream=False), so cancel lands during a blocking model
        call in the executor — the turn dies and no partial
        output is presented as a complete answer."""
        session = self._session()
        started = threading.Event()
        release = threading.Event()
        calls = []

        def blocking_completion(messages):
            calls.append("completion")
            started.set()
            release.wait(10)
            return _groq_text_response("partial never shown")

        session._chat_completion = blocking_completion

        async def main():
            turn = asyncio.create_task(session._brain_turn("hi"))
            await asyncio.to_thread(started.wait, 5)
            turn.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await turn
            release.set()
            await asyncio.sleep(0.2)
            self.assertEqual(len(calls), 1)
            self.assertEqual(self.transcripts, [])
            roles = [m.get("role") for m in session._history]
            self.assertNotIn("assistant", roles)

        asyncio.run(main())

    def test_cancel_during_deliberation_no_retry_after_cancel(self):
        """Case 2 (Groq): cancel during the deliberation guard's
        corrective retry — the budget stops cleanly, no further
        retry is injected after cancel."""
        session = self._session()
        second_started = threading.Event()
        second_release = threading.Event()
        calls = []

        def scripted(messages):
            calls.append("completion")
            if len(calls) == 1:
                return _groq_text_response(
                    "Let me think about which tool to use")
            second_started.set()
            second_release.wait(10)
            return _groq_text_response("never reached")

        session._chat_completion = scripted

        async def main():
            turn = asyncio.create_task(session._brain_turn("do it"))
            await asyncio.to_thread(second_started.wait, 5)
            turn.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await turn
            second_release.set()
            await asyncio.sleep(0.2)
            # Initial + corrective retry only; nothing after.
            self.assertEqual(len(calls), 2)
            self.assertEqual(self.transcripts, [])

        asyncio.run(main())


# ---------------------------------------------------------------- #
# 1/2. Local turn cancellation
# ---------------------------------------------------------------- #

class LocalCancelHarness(unittest.TestCase):
    def setUp(self):
        self.bus = BlockingBus()
        self._orig_tools = local_client.tools
        local_client.tools = self.bus
        self.addCleanup(setattr, local_client, "tools",
                        self._orig_tools)
        # BATCH 2 INTENTIONAL: the batch-2 split moved the turn
        # path's bare-`tools` readers out of the facade
        # (_extract_tool_call / _to_ollama_tools / _closest_tool_hints
        # in toolparse, _resident_priority_names / _tool_line in
        # budgets, _render_tool_protocol in render), so they read
        # their own module's `tools` binding, not the facade's.
        # Patch all four bindings to the same bus (B16/B47
        # dual-patch pattern); in production all four name the same
        # registry object, so this changes no product behaviour.
        from core.voice import (local_client_budgets,
                                local_client_builtin,
                                local_client_ollama,
                                local_client_plangoal,
                                local_client_render,
                                local_client_toolparse)
        self._orig_tools_toolparse = local_client_toolparse.tools
        local_client_toolparse.tools = self.bus
        self.addCleanup(setattr, local_client_toolparse, "tools",
                        self._orig_tools_toolparse)
        self._orig_tools_budgets = local_client_budgets.tools
        local_client_budgets.tools = self.bus
        self.addCleanup(setattr, local_client_budgets, "tools",
                        self._orig_tools_budgets)
        self._orig_tools_render = local_client_render.tools
        local_client_render.tools = self.bus
        self.addCleanup(setattr, local_client_render, "tools",
                        self._orig_tools_render)
        # BATCH 4 INTENTIONAL: the batch-4 split moved the turn
        # path's bare-`tools` readers out of the facade
        # (run_plan/run_goal in plangoal, _brain_turn_ollama /
        # _guard_state_answer in ollama, _brain_turn_builtin in
        # builtin), so they read their own module's `tools`
        # binding, not the facade's. Patch all seven bindings
        # to the same bus (B16/B47 dual-patch pattern, extended
        # from batch 2's four); in production all seven name the
        # same registry object, so this changes no product
        # behaviour.
        self._orig_tools_plangoal = local_client_plangoal.tools
        local_client_plangoal.tools = self.bus
        self.addCleanup(setattr, local_client_plangoal, "tools",
                        self._orig_tools_plangoal)
        self._orig_tools_ollama = local_client_ollama.tools
        local_client_ollama.tools = self.bus
        self.addCleanup(setattr, local_client_ollama, "tools",
                        self._orig_tools_ollama)
        self._orig_tools_builtin = local_client_builtin.tools
        local_client_builtin.tools = self.bus
        self.addCleanup(setattr, local_client_builtin, "tools",
                        self._orig_tools_builtin)
        self._orig_memory_block = local_client._memory_block
        local_client._memory_block = lambda user_text: ""
        self.addCleanup(setattr, local_client, "_memory_block",
                        self._orig_memory_block)
        # BATCH 4 INTENTIONAL: _brain_turn_ollama (ollama) and
        # _brain_turn_builtin (builtin) read bare `_memory_block`;
        # patch their module bindings too (same fake, restored).
        self._orig_memory_block_ollama = local_client_ollama._memory_block
        local_client_ollama._memory_block = lambda user_text: ""
        self.addCleanup(setattr, local_client_ollama, "_memory_block",
                        self._orig_memory_block_ollama)
        self._orig_memory_block_builtin = local_client_builtin._memory_block
        local_client_builtin._memory_block = lambda user_text: ""
        self.addCleanup(setattr, local_client_builtin, "_memory_block",
                        self._orig_memory_block_builtin)
        self._orig_est = local_client._est_tokens
        local_client._est_tokens = lambda text, model=None: max(
            1, len(str(text)) // 4)
        self.addCleanup(setattr, local_client, "_est_tokens",
                        self._orig_est)
        # BATCH 4 INTENTIONAL: _brain_turn_builtin (builtin) reads
        # bare `_est_tokens`; patch its module binding too.
        self._orig_est_builtin = local_client_builtin._est_tokens
        local_client_builtin._est_tokens = lambda text, model=None: max(
            1, len(str(text)) // 4)
        self.addCleanup(setattr, local_client_builtin, "_est_tokens",
                        self._orig_est_builtin)
        self._orig_protocol = local_client._render_tool_protocol
        local_client._render_tool_protocol = lambda *a, **k: ""
        self.addCleanup(setattr, local_client, "_render_tool_protocol",
                        self._orig_protocol)
        # BATCH 4 INTENTIONAL: _brain_turn_builtin (builtin) reads
        # bare `_render_tool_protocol`; patch its module binding
        # too (same stub, restored). This re-examines batch 2's
        # disclosed limitation: the moved method still resolves
        # render through its own module globals, so the facade
        # patch alone no longer steers it.
        self._orig_protocol_builtin = local_client_builtin._render_tool_protocol
        local_client_builtin._render_tool_protocol = lambda *a, **k: ""
        self.addCleanup(setattr, local_client_builtin, "_render_tool_protocol",
                        self._orig_protocol_builtin)
        self._orig_log = local_client.memory_store.log_message
        local_client.memory_store.log_message = lambda *a, **k: None
        self.addCleanup(setattr, local_client.memory_store,
                        "log_message", self._orig_log)
        self.transcripts = []

    def _session(self):
        session = object.__new__(LocalLiveSession)
        session._history = []
        session._llama = object()
        session._llama_unloaded = False
        session._notify_status = lambda *a, **k: None
        session.on_tool_call = None

        def _on_transcript(role, text):
            if role == "assistant":
                self.transcripts.append(text)

        session.on_transcript = _on_transcript

        async def _noop(*args, **kwargs):
            return None

        session._speak = _noop
        return session

    def _script_llama(self, session, replies):
        """replies: list of strings, or ('block', event, release)
        for a blocking model call. Returns the call count list."""
        queue = list(replies)
        calls = []

        def _fake(messages):
            calls.append("model")
            item = queue.pop(0)
            if isinstance(item, tuple):
                _, started, release = item
                started.set()
                release.wait(10)
                return "never reached"
            return item

        session._llama_chat = _fake
        return calls


class TestLocalCancel(LocalCancelHarness):
    def test_cancel_during_sync_tool_result_never_consumed(self):
        """Case 1 (local): the batch dispatch is synchronous, so
        a blocking tool parks the LOOP thread itself — a cancel
        can only arrive from another thread, exactly like the
        GUI thread cancelling a worker-loop turn (taskwake's
        shape). The watcher thread cancels via
        call_soon_threadsafe and releases the tool; the turn
        then dies via CancelledError. Honest mechanism note:
        the next model call may already be SCHEDULED when the
        cancel lands (run_in_executor schedules before the
        await that throws), so this test pins CONSUMPTION, not
        scheduling: the round-2 result is never spoken, never
        transcribed, never lands in history as an answer."""
        session = self._session()
        self._script_llama(session, [
            _tool_json("slow_tool", {}), "never reached"])

        async def main():
            loop = asyncio.get_running_loop()
            turn = asyncio.create_task(
                session._brain_turn_builtin("do it"))

            def watcher():
                if self.bus.tool_started.wait(10):
                    loop.call_soon_threadsafe(turn.cancel)
                    self.bus.tool_release.set()

            threading.Thread(target=watcher, daemon=True).start()
            with self.assertRaises(asyncio.CancelledError):
                await turn
            await asyncio.sleep(0.3)
            self.assertEqual(self.bus.count("slow_tool"), 1)
            self.assertEqual(self.transcripts, [])
            history_text = " ".join(
                str(m.get("content") or "") for m in session._history)
            self.assertNotIn("never reached", history_text)

        asyncio.run(main())

    def test_session_stop_stops_later_tools_in_the_same_batch(self):
        """Case 1 (local, Phase 4 hardening): without a
        per-item check, a stop landing during item N of a
        synchronous batch could not stop item N+1 (no await
        between items to notice it, and a queued task.cancel()
        cannot run while the loop thread is parked inside a
        blocking tool). The batch loop now checks the
        session's _running flag before each dispatch — set
        False synchronously by stop() from any thread, the
        same shape as the live batch's check: tool_a, queued
        after the stopped slow_tool, never runs, and the turn
        dies via CancelledError."""
        session = self._session()
        session._running = True
        self._script_llama(session, [
            _batch_json([("slow_tool", {}), ("tool_a", {})]),
            "never reached"])

        async def main():
            turn = asyncio.create_task(
                session._brain_turn_builtin("do both"))

            def watcher():
                if self.bus.tool_started.wait(10):
                    session._running = False
                    self.bus.tool_release.set()

            threading.Thread(target=watcher, daemon=True).start()
            with self.assertRaises(asyncio.CancelledError):
                await turn
            await asyncio.sleep(0.3)
            self.assertEqual(self.bus.count("slow_tool"), 1)
            self.assertEqual(self.bus.count("tool_a"), 0)
            self.assertEqual(self.transcripts, [])

        asyncio.run(main())

    def test_cancel_during_repair_escalation_survives(self):
        """Case 2 (local): cancel during a repair round's model
        call — no further repair is injected, and the Phase 3
        escalation record from the repair is NOT silently
        swallowed by the cancellation."""
        session = self._session()
        repair_started = threading.Event()
        repair_release = threading.Event()
        calls = self._script_llama(session, [
            _batch_json([("tool_a", {}), ("tool_b", {})]),
            ("block", repair_started, repair_release)])

        async def main():
            turn = asyncio.create_task(
                session._brain_turn_builtin("do a then b"))
            await asyncio.to_thread(repair_started.wait, 5)
            turn.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await turn
            repair_release.set()
            await asyncio.sleep(0.2)
            # Repair round started (2nd model call), nothing more.
            self.assertEqual(len(calls), 2)
            self.assertEqual(self.bus.count("tool_a"), 1)
            self.assertEqual(self.bus.count("tool_b"), 1)
            # The repair's escalation record survives the cancel.
            escalations = getattr(session, "_last_profile_escalations",
                                  ())
            self.assertTrue(escalations)
            self.assertEqual(self.transcripts, [])

        asyncio.run(main())


# ---------------------------------------------------------------- #
# 1/6. Live batch cancellation + receipts
# ---------------------------------------------------------------- #

class LiveCancelHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tool_receipts.install_store(Path(self._tmp.name) / "r.jsonl")
        self.addCleanup(tool_receipts.reset_default)
        # Lab has no google-genai package, so live_client.types
        # is None here; the delivery tail builds FunctionResponse
        # objects from it. Stub only the constructor shape — the
        # filtering logic under test (which ids are delivered)
        # is production code, not the stub.
        self._orig_types = live_client.types
        _fake_types = SimpleNamespace(
            FunctionResponse=lambda **kw: SimpleNamespace(**kw))
        live_client.types = _fake_types
        self.addCleanup(setattr, live_client, "types",
                        self._orig_types)
        # INTENTIONAL (skills split B47): _run_tool_batch moved to
        # the receive mixin and reads types from its own globals —
        # patch both bindings (B16 pattern, same fake, both restored).
        self._orig_types_recv = live_client_receive_mod.types
        live_client_receive_mod.types = _fake_types
        self.addCleanup(setattr, live_client_receive_mod, "types",
                        self._orig_types_recv)

    def _session(self, execute):
        session = object.__new__(GeminiLiveSession)
        session._cancelled_call_ids = set()
        session._running = True
        session.on_tool_call = None
        session.on_transcript = None
        session._execute_tool_sync = execute
        self.delivered = []

        async def _deliver(responses):
            self.delivered.extend(responses)

        session._deliver_tool_responses = _deliver
        return session

    def _fc(self, cid, args):
        return SimpleNamespace(id=cid, name="some_tool", args=args)

    def _receipt_outcomes(self):
        path = Path(self._tmp.name) / "r.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec["type"] == "outcome":
                out.append(rec)
        return out


class TestLiveCancel(LiveCancelHarness):
    def test_pre_dispatch_skip_records_cancelled_receipt(self):
        """Cases 1 + 6 (live): a call cancelled while earlier
        calls ran is skipped in the dispatch loop — never
        dispatched — and leaves a 'cancelled' receipt
        (dispatched=False), not a silent gap."""
        executed = []

        def execute(fc):
            executed.append(fc.id)
            return {"status": "success", "result": "ok-" + fc.id}

        session = self._session(execute)
        session._cancelled_call_ids = {"c2"}
        batch = [self._fc("c1", {"n": 1}), self._fc("c2", {"n": 2}),
                 self._fc("c3", {"n": 3})]
        asyncio.run(session._run_tool_batch(batch))
        self.assertEqual(executed, ["c1", "c3"])
        delivered_ids = [r.id for r in self.delivered]
        self.assertEqual(delivered_ids, ["c1", "c3"])
        cancelled = [o for o in self._receipt_outcomes()
                     if o["status"] == "cancelled"]
        self.assertEqual(len(cancelled), 1)
        self.assertFalse(cancelled[0]["dispatched"])
        # Cancelled-before-dispatch is safe to re-run.
        store = tool_receipts._get_store()
        self.assertIsNone(store.find_unknown(tool="some_tool",
                                             args={"n": 2}))

    def test_stop_mid_batch_abandons_pending_delivery(self):
        """Case 1 (live, stop): the session stops mid-batch —
        the pending delivery is abandoned, later calls never
        dispatch, and nothing is delivered into a dying session."""
        executed = []

        def execute(fc):
            executed.append(fc.id)
            session_ref[0]._running = False
            return {"status": "success", "result": "ok-" + fc.id}

        session_ref = [None]
        session = self._session(execute)
        session_ref[0] = session
        batch = [self._fc("c1", {"n": 1}), self._fc("c2", {"n": 2}),
                 self._fc("c3", {"n": 3})]
        asyncio.run(session._run_tool_batch(batch))
        self.assertEqual(executed, ["c1"])
        self.assertEqual(self.delivered, [])

    def test_delivery_drop_for_call_cancelled_during_execution(self):
        """Case 1 (live, late cancel): a call that ran but was
        cancelled during its own execution has its result
        DROPPED at delivery — the model never acts on a plan
        it already abandoned."""
        executed = []

        def execute(fc):
            executed.append(fc.id)
            session_ref[0]._cancelled_call_ids.add(fc.id)
            return {"status": "success", "result": "ok-" + fc.id}

        session_ref = [None]
        session = self._session(execute)
        session_ref[0] = session
        batch = [self._fc("c1", {"n": 1}), self._fc("c2", {"n": 2})]
        asyncio.run(session._run_tool_batch(batch))
        # Both ran (honest limit: threads cannot be killed),
        # but NEITHER result was delivered.
        self.assertEqual(executed, ["c1", "c2"])
        self.assertEqual(self.delivered, [])


# ---------------------------------------------------------------- #
# 5. Double-cancel / cancel-after-complete
# ---------------------------------------------------------------- #

class TestDoubleCancel(unittest.TestCase):
    def test_brain_stop_twice_is_a_safe_noop(self):
        session = object.__new__(BrainSession)
        session._stop_requested = False
        stops = []

        class FakeInner:
            def stop(self):
                stops.append("stop")

        session._inner = FakeInner()
        session.stop()
        session.stop()
        self.assertTrue(session._stop_requested)
        self.assertEqual(stops, ["stop", "stop"])

    def test_local_stop_twice_is_a_safe_noop(self):
        session = object.__new__(LocalLiveSession)
        session._running = True
        session._setup_cancel = threading.Event()
        session._session_tasks = []
        session._turn_tasks = set()
        session.stop()
        session.stop()
        self.assertFalse(session._running)
        self.assertTrue(session._setup_cancel.is_set())

    def test_groq_stop_twice_is_a_safe_noop(self):
        session = object.__new__(GroqSession)
        session._running = True
        session._speech_helper = None
        session._session_tasks = []
        session._turn_tasks = set()
        session.stop()
        session.stop()
        self.assertFalse(session._running)

    def test_live_stop_twice_clears_handle_and_never_errors(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        orig = live_client._resume_path_override
        live_client._resume_path_override = Path(tmp.name) / "handle.json"
        self.addCleanup(setattr, live_client, "_resume_path_override",
                        orig)
        # INTENTIONAL (skills split B47): _resume_path moved to the
        # resume module and reads the override from its own globals
        # — patch both bindings (B16 pattern, both restored).
        orig_recv = live_client_resume_mod._resume_path_override
        live_client_resume_mod._resume_path_override = Path(tmp.name) / "handle.json"
        self.addCleanup(setattr, live_client_resume_mod,
                        "_resume_path_override", orig_recv)
        (Path(tmp.name) / "handle.json").write_text("{}", encoding="utf-8")
        session = object.__new__(GeminiLiveSession)
        session._running = True
        session._send_task = None
        session._receive_task = None
        session._tool_worker_task = None
        session.stop()
        session.stop()
        self.assertFalse(session._running)
        self.assertFalse((Path(tmp.name) / "handle.json").exists())

    def test_turn_task_double_cancel_never_re_executes(self):
        ran = []

        async def main():
            async def work():
                ran.append("ran")
                await asyncio.sleep(60)

            task = asyncio.create_task(work())
            await asyncio.sleep(0)
            self.assertTrue(task.cancel())
            # Second cancel: already cancelling — no error,
            # and the work is never re-executed.
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(ran, ["ran"])

        asyncio.run(main())

    def test_cancel_after_complete_is_a_noop(self):
        async def main():
            async def quick():
                return "done"

            task = asyncio.create_task(quick())
            self.assertEqual(await task, "done")
            # Cancelling a finished task: False, no error,
            # result intact, nothing re-run.
            self.assertFalse(task.cancel())
            self.assertEqual(task.result(), "done")

        asyncio.run(main())


# ---------------------------------------------------------------- #
# 4. Standby then resume: explicit only, never automatic
# ---------------------------------------------------------------- #

class TestNoAutoResume(unittest.TestCase):
    def test_interrupted_task_reported_never_auto_run(self):
        """Ledger rule: a paused task is REPORTED by
        list_interrupted, and merely querying it (even from a
        fresh instance, i.e. after a restart) executes nothing."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ledger = tl.TaskLedger(path=Path(tmp.name) / "ledger.json")
        calls = []

        def execute(tool, args):
            calls.append(tool)
            return {"status": "success", "result": "ok"}

        coord = tc.TurnCoordinator(executor=execute, ledger=ledger)
        steps = [StepDef.from_mapping({"tool": t, "args": {}})
                 for t in ("a", "b", "c")]
        task = coord.new_task("t", "routine", steps,
                              turn_text="run routine t",
                              brain_id="local_text")

        def pause_after_first(task_obj, outcome):
            coord.pause_task(task_obj.id)

        coord.run_task(task, on_step=pause_after_first)
        self.assertEqual(calls, ["a"])
        summaries = ledger.list_interrupted()
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["task_id"], task.id)
        self.assertEqual(summaries[0]["remaining_labels"], ["b", "c"])
        # Querying again — and from a fresh instance (restart) —
        # runs nothing: no auto-resume exists on this path.
        ledger.list_interrupted()
        restarted = tl.TaskLedger(path=Path(tmp.name) / "ledger.json")
        restarted.list_interrupted()
        self.assertEqual(calls, ["a"])

    def test_all_read_remainder_helper_is_not_a_run(self):
        """remaining_all_reads is a pure predicate: True for an
        all-read remainder, False otherwise — and calling it
        executes nothing (auto-resume has no caller)."""
        self.assertTrue(tl.remaining_all_reads(
            [{"tool": "get_current_time_date"}]))
        self.assertFalse(tl.remaining_all_reads(
            [{"tool": "get_current_time_date"}, {"tool": "get_weather"}]))
        self.assertFalse(tl.remaining_all_reads(
            [{"tool": "send_email"}]))
        self.assertFalse(tl.remaining_all_reads([]))

    def test_explicit_resume_is_the_only_resume(self):
        """Rebuilding a task from its record requires an explicit
        caller: rebuild_task returns the task object but does
        not run it (running is a separate, explicit step)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ledger = tl.TaskLedger(path=Path(tmp.name) / "ledger.json")
        calls = []

        def execute(tool, args):
            calls.append(tool)
            return {"status": "success", "result": "ok"}

        coord = tc.TurnCoordinator(executor=execute, ledger=ledger)
        steps = [StepDef.from_mapping({"tool": t, "args": {}})
                 for t in ("a", "b")]
        task = coord.new_task("t", "routine", steps,
                              turn_text="run routine t",
                              brain_id="local_text")

        def pause_after_first(task_obj, outcome):
            coord.pause_task(task_obj.id)

        coord.run_task(task, on_step=pause_after_first)
        record = ledger.get(task.id)
        rebuilt = ledger.rebuild_task(record)
        self.assertIsNotNone(rebuilt)
        # Rebuilding alone executed nothing further.
        self.assertEqual(calls, ["a"])


if __name__ == "__main__":
    unittest.main()
