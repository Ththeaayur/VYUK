"""Turn-truthfulness batch (seventh audit B01 + sixth audit
B02/B04/B06/B08).

After any interrupted or exhausted turn, the assistant must report
what actually happened -- no lost outcomes, no duplicate side
effects, no unverified answers.

7th-B01: a partial builtin batch must not lose earlier successes
when a later item needs repair.
6th-B02: a round-exhausted Groq turn reports its completed actions
(partial, honestly) instead of the generic loop apology.
6th-B04: history windowing/capping never carries a tool call
without ALL of its required responses -- partial exchanges drop
whole, never half-retained.
6th-B06: attempted is not succeeded -- state-question guards and
"Done." require a fresh SUCCESSFUL observation.
6th-B08: local paths record a compact structured action trace per
turn and surface the latest trace in the next turn's prompt.

Tests use fake model replies and a counting fake tool bus -- no
network, no real funnel tools, no model.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from core.voice import cloud_text_client  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.voice import cloud_text_turn as cloud_text_turn_mod  # B39 INTENTIONAL: cloud split — turn methods resolve `tools` / `_max_tool_rounds` in turn-mixin globals; tests patch both bindings
from core.voice import local_client  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.voice.cloud_text_client import GroqSession  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.groq_action_journal import GroqActionJournal
from core.voice.local_client import LocalLiveSession  # Phase 3 V1b INTENTIONAL: moved to core/voice/


def _decl(name):
    return {"name": name, "description": "fake " + name,
            "parameters": {"type": "object", "properties": {}}}


class FakeBus:
    """Counting stand-in for tools.execute + get_declarations."""

    def __init__(self, names=("tool_a", "tool_b",
                              "get_current_time_date", "battery_info")):
        self.names = list(names)
        self.calls = []

    def get_declarations(self):
        return [_decl(n) for n in self.names]

    def execute(self, name, args):
        self.calls.append((name, dict(args)))
        if name == "tool_b":
            return {"status": "refused", "kind": "validation",
                    "validation_error": True,
                    "message": "bad args for tool_b"}
        if name in ("get_current_time_date", "battery_info"):
            return {"status": "failure", "kind": "handler_error",
                    "message": "state tool boom"}
        return {"status": "success", "result": "did-" + name}

    def count(self, name):
        return sum(1 for n, _ in self.calls if n == name)


def _tool_json(name, args=None):
    return json.dumps({"tool": name, "arguments": args or {}})


def _batch_json(calls):
    return json.dumps([{"tool": n, "arguments": a} for n, a in calls])


class LocalHarness(unittest.TestCase):
    """Drives _brain_turn_builtin/_brain_turn_ollama with stubs."""

    def setUp(self):
        self.bus = FakeBus()
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
        """Record each call's messages; answer from the queue."""
        queue = list(replies)
        seen = []

        def _fake(messages):
            seen.append([(m.get("role", ""), str(m.get("content") or ""))
                         for m in messages])
            return queue.pop(0)

        session._llama_chat = _fake
        return seen

    def _turn_builtin(self, session, user_text):
        asyncio.run(session._brain_turn_builtin(user_text))

    def _turn_ollama(self, session, user_text):
        asyncio.run(session._brain_turn_ollama(user_text))


class TestBatchRepairKeepsSuccesses(LocalHarness):
    def test_batch_repair_next_round_sees_earlier_success(self):
        """7th-B01: A succeeds, B is rejected in the same batch --
        the next model call receives A's outcome AND B's repair
        error; A executes exactly once."""
        session = self._session()
        seen = self._script_llama(session, [
            _batch_json([("tool_a", {}), ("tool_b", {})]),
            "All done.",
        ])
        self._turn_builtin(session, "do a then b")
        self.assertEqual(self.bus.count("tool_a"), 1)
        self.assertEqual(self.bus.count("tool_b"), 1)
        self.assertEqual(len(seen), 2)
        round2 = " ".join(content for _, content in seen[1])
        self.assertIn("did-tool_a", round2)
        self.assertIn("Tool call rejected", round2)
        self.assertIn("bad args for tool_b", round2)
        self.assertIn("All done.", self.transcripts)
        actions = session._turn_action_trace[-1]["actions"]
        by_tool = {a["tool"]: a["status"] for a in actions}
        self.assertEqual(by_tool["tool_a"], "succeeded")
        self.assertEqual(by_tool["tool_b"], "rejected")

    def test_batch_all_success_normal_path_single_results_message(self):
        """Guard for the 7th-B01 restructure: the all-success batch
        still feeds ONE 'Tool results:' message, as before."""
        session = self._session()
        seen = self._script_llama(session, [
            _batch_json([("tool_a", {})]),
            "Finished.",
        ])
        self._turn_builtin(session, "do a")
        self.assertEqual(len(seen), 2)
        results_msgs = [c for r, c in seen[1]
                        if r == "user" and c.startswith("Tool results:")]
        self.assertEqual(len(results_msgs), 1)
        self.assertIn("did-tool_a", results_msgs[0])


class TestStateGuardRequiresSuccess(LocalHarness):
    def test_failed_time_tool_cannot_fabricate_builtin(self):
        """6th-B06 (builtin): the time tool fails, the model then
        claims '3:45 PM' from nothing -- the guard fires (a failed
        tool is no evidence) and replaces the fabrication with the
        failure's honest rendering."""
        session = self._session()
        self._script_llama(session, [
            _tool_json("get_current_time_date"),
            "The time is 3:45 PM.",
        ])
        self._turn_builtin(session, "what time is it")
        # Model call + forced guard repair = 2 executions.
        self.assertEqual(self.bus.count("get_current_time_date"), 2)
        final = self.transcripts[-1]
        self.assertNotIn("3:45", final)
        self.assertIn("Something went wrong", final)

    def test_failed_tool_never_earns_done_builtin(self):
        """6th-B06 (builtin): a failed tool followed by an empty
        reply must not end in a bare 'Done.'."""
        session = self._session()
        self._script_llama(session, [
            _tool_json("battery_info"),
            "",
        ])
        self._turn_builtin(session, "check the battery")
        final = self.transcripts[-1]
        self.assertNotEqual(final, "Done.")
        self.assertIn("Something went wrong", final)

    def test_failed_time_tool_cannot_fabricate_ollama(self):
        """6th-B06 (ollama): same guard rule on the Ollama path."""
        session = self._session()
        calls = {"n": 0}

        def _fake_ollama(messages, ollama_tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"message": {
                    "content": "",
                    "tool_calls": [{"function": {
                        "name": "get_current_time_date",
                        "arguments": {}}}]},
                        }
            return {"message": {
                "content": "The time is 3:45 PM.", "tool_calls": []}}

        session._ollama_chat = _fake_ollama
        self._turn_ollama(session, "what time is it")
        self.assertEqual(self.bus.count("get_current_time_date"), 2)
        final = self.transcripts[-1]
        self.assertNotIn("3:45", final)
        self.assertIn("Something went wrong", final)


class TestLocalActionTrace(LocalHarness):
    def test_trace_recorded_and_next_turn_sees_it(self):
        """6th-B08 (builtin): a multi-tool turn's trace records
        statuses, and the NEXT turn's system prompt carries it."""
        session = self._session()
        seen = self._script_llama(session, [
            _tool_json("tool_a"),
            "All done.",
        ])
        self._turn_builtin(session, "do a")
        actions = session._turn_action_trace[-1]["actions"]
        self.assertEqual(
            [(a["tool"], a["status"]) for a in actions],
            [("tool_a", "succeeded")])
        # Second turn: inspect the system prompt it was built with.
        seen2 = self._script_llama(session, ["Nothing further."])
        self._turn_builtin(session, "what did you do")
        system = seen2[0][0][1]
        self.assertIn("tool_a", system)
        self.assertIn("succeeded", system)

    def test_trace_records_failure_and_rejection(self):
        """6th-B08: failed and rejected calls are traced with
        their own statuses, never silently as successes."""
        session = self._session()
        self._script_llama(session, [
            _batch_json([("tool_a", {}), ("tool_b", {})]),
            "Gave up.",
        ])
        self._turn_builtin(session, "do a then b")
        actions = session._turn_action_trace[-1]["actions"]
        by_tool = {a["tool"]: a["status"] for a in actions}
        self.assertEqual(by_tool["tool_a"], "succeeded")
        self.assertEqual(by_tool["tool_b"], "rejected")

    def test_ollama_trace_recorded_and_next_turn_sees_it(self):
        """6th-B08 (ollama): same trace + next-turn note."""
        session = self._session()
        calls = {"n": 0}
        seen = []

        def _fake_ollama(messages, ollama_tools):
            seen.append([(m.get("role", ""), str(m.get("content") or ""))
                         for m in messages])
            calls["n"] += 1
            if calls["n"] == 1:
                return {"message": {
                    "content": "",
                    "tool_calls": [{"function": {
                        "name": "tool_a", "arguments": {}}}]},
                        }
            return {"message": {"content": "All done.",
                                "tool_calls": []}}

        session._ollama_chat = _fake_ollama
        self._turn_ollama(session, "do a")
        actions = session._turn_action_trace[-1]["actions"]
        self.assertEqual(
            [(a["tool"], a["status"]) for a in actions],
            [("tool_a", "succeeded")])
        calls["n"] = 0
        seen2_start = len(seen)

        def _fake_ollama_plain(messages, ollama_tools):
            seen.append([(m.get("role", ""), str(m.get("content") or ""))
                         for m in messages])
            return {"message": {"content": "Nothing further.",
                                "tool_calls": []}}

        session._ollama_chat = _fake_ollama_plain
        self._turn_ollama(session, "what did you do")
        system = seen[seen2_start][0][1]
        self.assertIn("tool_a", system)
        self.assertIn("succeeded", system)


def _groq_tool_response(calls):
    """One completion carrying tool calls: [(id, name, args)]."""
    return (200, {"choices": [{"message": {
        "content": "",
        "tool_calls": [
            {"id": cid,
             "function": {"name": name, "arguments": json.dumps(args)}}
            for cid, name, args in calls]}}]})


def _groq_text_response(text):
    return (200, {"choices": [{"message": {
        "content": text, "tool_calls": []}}]})


class GroqHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bus = FakeBus()
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

    def _script(self, session, responses):
        queue = list(responses)

        def _fake(messages):
            return queue.pop(0)

        session._chat_completion = _fake

    def _turn(self, session, text="do the thing"):
        asyncio.run(session._brain_turn(text))


class TestGroqRoundExhaustion(GroqHarness):
    def test_exhaustion_reports_completed_actions_not_apology(self):
        """6th-B02: max rounds with a successful tool call each
        time -- the final answer reports the accomplished actions
        as partial, never the generic loop fallback."""
        session = self._session()
        orig_rounds = cloud_text_client._max_tool_rounds
        cloud_text_client._max_tool_rounds = lambda: 2
        self.addCleanup(setattr, cloud_text_client, "_max_tool_rounds",
                        orig_rounds)
        # B39 INTENTIONAL: _brain_turn resolves _max_tool_rounds in
        # turn-mixin globals now — patch both bindings (dual-patch
        # pattern), same fake, both restored.
        orig_turn_rounds = cloud_text_turn_mod._max_tool_rounds
        cloud_text_turn_mod._max_tool_rounds = lambda: 2
        self.addCleanup(setattr, cloud_text_turn_mod, "_max_tool_rounds",
                        orig_turn_rounds)
        self._script(session, [
            _groq_tool_response([("call_1", "tool_a", {})]),
            # Different args: identical args would trip the
            # no-progress breaker instead of exhausting rounds.
            _groq_tool_response([("call_2", "tool_a", {"n": 2})]),
        ])
        self._turn(session)
        self.assertEqual(self.bus.count("tool_a"), 2)
        final = session._history[-1]["content"]
        self.assertIn("tool_a", final)
        self.assertIn("partway", final)
        self.assertIn("not done yet", final)
        self.assertNotIn("got stuck in a loop", final)

    def test_exhaustion_with_nothing_done_keeps_fallback(self):
        """6th-B02 guard: when no action completed, the generic
        fallback is still the honest answer."""
        session = self._session()
        summary = session._groq_exhaustion_summary([])
        self.assertIn("got stuck in a loop", summary)

    def test_exhaustion_summary_falls_back_without_journal(self):
        """6th-B02: with no journal available, the in-memory
        accomplished list is the outcome source."""
        session = self._session()
        session._action_journal = None
        summary = session._groq_exhaustion_summary(
            ["tool_a: did-tool_a"])
        self.assertIn("tool_a", summary)
        self.assertIn("partway", summary)


class TestHistoryExchangeCompleteness(GroqHarness):
    def _assistant_calls(self, *ids):
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": i, "function": {"name": "tool_a",
                                                     "arguments": "{}"}}
                               for i in ids]}

    def _tool_msg(self, call_id):
        return {"role": "tool", "tool_call_id": call_id,
                "content": "result"}

    def test_windowed_drops_partial_exchange_whole(self):
        """6th-B04: assistant with two calls, only one result in
        the window -- the exchange drops whole: no half-retained
        call, and the orphan result goes with it."""
        session = self._session()
        session._history = [
            {"role": "user", "content": "hi"},
            self._assistant_calls("c1", "c2"),
            self._tool_msg("c1"),
        ]
        window = session._windowed_history()
        self.assertEqual([m.get("role") for m in window], ["user"])
        self.assertFalse(any(m.get("tool_calls") for m in window))

    def test_windowed_keeps_complete_exchange(self):
        """6th-B04 positive control: a complete two-call exchange
        passes through unchanged."""
        session = self._session()
        session._history = [
            {"role": "user", "content": "hi"},
            self._assistant_calls("c1", "c2"),
            self._tool_msg("c1"),
            self._tool_msg("c2"),
        ]
        window = session._windowed_history()
        self.assertEqual(len(window), 4)
        self.assertTrue(any(m.get("tool_calls") for m in window))

    def test_windowed_drops_partial_exchange_in_middle(self):
        """6th-B04: validation covers the whole window, not just
        its leading message."""
        session = self._session()
        session._history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            self._assistant_calls("c1", "c2"),
            self._tool_msg("c1"),
            {"role": "user", "content": "and then"},
        ]
        window = session._windowed_history()
        self.assertFalse(any(m.get("tool_calls") for m in window))
        self.assertEqual(window[-1]["content"], "and then")

    def test_cap_drops_partial_exchange_at_front(self):
        """6th-B04: after trimming to HISTORY_CAP, a partial
        exchange landing first is dropped whole."""
        session = self._session()
        fillers = []
        for i in range(22):
            fillers.append({"role": "user" if i % 2 == 0 else "assistant",
                            "content": "filler %d" % i})
        session._history = (
            [{"role": "user", "content": "u0"},
             {"role": "assistant", "content": "a0"},
             self._assistant_calls("c1", "c2"),
             self._tool_msg("c1")]
            + fillers)
        self.assertEqual(len(session._history), 26)
        session._cap_history()
        self.assertFalse(any(m.get("tool_calls")
                             for m in session._history))
        self.assertEqual(session._history[0]["content"], "filler 0")

    def test_cap_keeps_complete_exchange_at_front(self):
        """6th-B04 positive control for _cap_history."""
        session = self._session()
        fillers = []
        for i in range(21):
            fillers.append({"role": "user" if i % 2 == 0 else "assistant",
                            "content": "filler %d" % i})
        session._history = (
            [{"role": "user", "content": "u0"},
             {"role": "assistant", "content": "a0"},
             self._assistant_calls("c1", "c2"),
             self._tool_msg("c1"),
             self._tool_msg("c2")]
            + fillers)
        self.assertEqual(len(session._history), 26)
        session._cap_history()
        self.assertTrue(any(m.get("tool_calls")
                            for m in session._history))


class TestGroqStateGuardRequiresSuccess(GroqHarness):
    def test_failed_time_tool_cannot_fabricate_groq(self):
        """6th-B06 (Groq): the time tool fails, the model then
        claims '3:45 PM' -- the guard fires and replaces it."""
        session = self._session()
        self._script(session, [
            _groq_tool_response([("call_1", "get_current_time_date", {})]),
            _groq_text_response("The time is 3:45 PM."),
        ])
        self._turn(session, "what time is it")
        self.assertEqual(self.bus.count("get_current_time_date"), 2)
        final = session._history[-1]["content"]
        self.assertNotIn("3:45", final)
        self.assertIn("Something went wrong", final)

    def test_failed_tool_never_earns_bare_done_groq(self):
        """6th-B06 (Groq): a failed tool followed by an empty reply
        must not end in a bare 'Done.'."""
        session = self._session()
        self._script(session, [
            _groq_tool_response([("call_1", "battery_info", {})]),
            _groq_text_response(""),
        ])
        self._turn(session, "check the battery")
        final = session._history[-1]["content"]
        self.assertNotEqual(final, "Done.")
        self.assertIn("problem", final)


if __name__ == "__main__":
    unittest.main()
