"""Phase 0 (fourth audit C01 + C02).

C01: the connect gate must check the SELECTED provider's key —
a Groq-only setup (Groq key present, Gemini key empty) must not
be refused for lacking a Gemini key, and messages must name the
provider and the missing field.

C02: Groq turns keep a durable per-action journal that survives
the turn-failure history rollback, and a retry cannot blindly
re-execute a completed action from a failed turn. Tests use a
fake completion bus and a counting fake tool executor — no
network, no real funnel tools.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from core.voice import cloud_text_client  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.voice import cloud_text_turn as cloud_text_turn_mod  # B39 INTENTIONAL: cloud split — turn methods resolve `tools` / `_max_tool_rounds` in turn-mixin globals; tests patch both bindings
from core.voice.cloud_text_client import GroqSession  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.config import AppConfig
from core.groq_action_journal import GroqActionJournal


def _tool_response(calls):
    """One completion carrying tool calls: [(id, name, args)]."""
    return (200, {"choices": [{"message": {
        "content": "",
        "tool_calls": [
            {"id": cid,
             "function": {"name": name, "arguments": json.dumps(args)}}
            for cid, name, args in calls]}}]})


def _text_response(text):
    return (200, {"choices": [{"message": {
        "content": text, "tool_calls": []}}]})


class FakeBus:
    """Counting stand-in for tools.execute."""

    def __init__(self, failures=()):
        self.calls: list[tuple[str, dict]] = []
        self.failures = set(failures)

    def execute(self, name, args):
        self.calls.append((name, dict(args)))
        if name in self.failures:
            return {"status": "failure", "message": "boom"}
        return {"status": "success",
                "result": f"did-{name}-{len(self.calls)}"}

    def count(self, name):
        return sum(1 for n, _ in self.calls if n == name)


class TestC01KeyGate(unittest.TestCase):
    def _cfg(self, brain_engine, cloud_provider, api_key, groq_api_key):
        cfg = AppConfig()
        cfg.brain_engine = brain_engine
        cfg.cloud_provider = cloud_provider
        cfg.api_key = api_key
        cfg.groq_api_key = groq_api_key
        return cfg

    def test_groq_only_setup_passes(self):
        cfg = self._cfg("gemini", "groq", "", "g-key")
        self.assertEqual(cfg.selected_provider(), "groq")
        self.assertEqual(cfg.selected_provider_key_field(), "groq_api_key")
        self.assertTrue(cfg.selected_provider_key_present())

    def test_reverse_setup_refuses_with_gemini_message(self):
        cfg = self._cfg("gemini", "gemini", "", "g-key")
        self.assertEqual(cfg.selected_provider(), "gemini")
        self.assertFalse(cfg.selected_provider_key_present())
        self.assertIn("Gemini", cfg.missing_provider_key_message())

    def test_local_needs_no_key(self):
        cfg = self._cfg("local", "groq", "", "")
        self.assertEqual(cfg.selected_provider(), "local")
        self.assertIsNone(cfg.selected_provider_key_field())
        self.assertTrue(cfg.selected_provider_key_present())

    def test_groq_missing_key_names_groq(self):
        cfg = self._cfg("gemini", "groq", "x", "")
        self.assertFalse(cfg.selected_provider_key_present())
        msg = cfg.missing_provider_key_message()
        self.assertIn("Groq", msg)
        self.assertIn("Groq key", msg)

    def test_unknown_provider_falls_back_to_gemini(self):
        cfg = self._cfg("gemini", "weird", "x", "")
        self.assertEqual(cfg.selected_provider(), "gemini")
        self.assertTrue(cfg.selected_provider_key_present())

    def test_whitespace_key_counts_absent(self):
        cfg = self._cfg("gemini", "groq", "", "   ")
        self.assertFalse(cfg.selected_provider_key_present())

    def test_gate_uses_helper_not_old_check(self):
        """The connect gate must resolve through the helper; the
        old fixed-Gemini-key check must not come back."""
        # GUI split #11 batch 2 INTENTIONAL: _start_connection moved
        # verbatim to window_session_connection.py.
        path = Path(__file__).resolve().parent.parent \
            / "gui" / "window_session_connection.py"
        text = path.read_text(encoding="utf-8")
        start = text.index("def _start_connection")
        rest = text[start:]
        nxt = rest.find("\n    def ", 1)
        body = rest if nxt < 0 else rest[:nxt]
        self.assertIn("selected_provider_key_present", body)
        self.assertNotIn("not config.api_key", body)
        self.assertNotIn("Add your key to use the cloud brain", text)


class GroqTurnTestCase(unittest.TestCase):
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

    def _session(self):
        session = GroqSession()
        session._action_journal = GroqActionJournal(
            path=Path(self._tmp.name) / "journal.json")
        session._system_prompt = "test system"

        async def _noop(*args, **kwargs):
            return None

        session._speak = _noop
        session._maybe_auto_summarize = _noop
        return session

    def _script(self, session, responses):
        queue = list(responses)

        def _fake(messages):
            return queue.pop(0)

        session._chat_completion = _fake

    def _turn(self, session):
        asyncio.run(session._brain_turn("do the thing"))

    def _journal_events(self, session):
        return session._action_journal.events()

    def test_503_after_tool_success_journal_survives_and_retry_blocked(self):
        """The brief's regression: tool succeeds, second response
        fails 503 → history rolls back, journal keeps the completed
        action, and the retry turn cannot blindly re-execute it."""
        session = self._session()
        self._script(session, [
            _tool_response([("call_1", "fake_tool", {"x": 1})]),
            (503, {"error": {"message": "overloaded"}}),
        ])
        self._turn(session)
        self.assertEqual(self.bus.count("fake_tool"), 1)
        self.assertEqual(session._history, [])
        events = self._journal_events(session)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "success")
        self.assertTrue(events[0]["turn_failed"])

        # Retry turn: same tool+args → blocked, surfaced, no execution.
        self._script(session, [
            _tool_response([("call_2", "fake_tool", {"x": 1})]),
            _text_response("done."),
        ])
        self._turn(session)
        self.assertEqual(self.bus.count("fake_tool"), 1)
        events = self._journal_events(session)
        self.assertEqual(len(events), 1)
        self.assertIsNotNone(events[0]["surfaced_in_turn"])
        tool_msgs = [m for m in session._history
                     if m.get("role") == "tool"]
        self.assertTrue(tool_msgs)
        self.assertIn("already completed", tool_msgs[0]["content"])

        # A LATER turn, after surfacing, may run it again (informed).
        self._script(session, [
            _tool_response([("call_3", "fake_tool", {"x": 1})]),
            _text_response("done."),
        ])
        self._turn(session)
        self.assertEqual(self.bus.count("fake_tool"), 2)

    def test_same_turn_second_attempt_stays_blocked(self):
        session = self._session()
        self._script(session, [
            _tool_response([("call_1", "fake_tool", {"x": 1})]),
            (503, {"error": {"message": "overloaded"}}),
        ])
        self._turn(session)
        self.assertEqual(self.bus.count("fake_tool"), 1)
        # X blocked, Y runs, X again in the SAME turn stays blocked.
        self._script(session, [
            _tool_response([("call_2", "fake_tool", {"x": 1})]),
            _tool_response([("call_3", "other_tool", {"y": 2})]),
            _tool_response([("call_4", "fake_tool", {"x": 1})]),
            _text_response("done."),
        ])
        self._turn(session)
        self.assertEqual(self.bus.count("fake_tool"), 1)
        self.assertEqual(self.bus.count("other_tool"), 1)

    def test_successful_turn_reuse_is_not_blocked(self):
        """Events from a SUCCESSFUL turn never trigger the guard:
        history retains them, so a later identical request runs."""
        session = self._session()
        self._script(session, [
            _tool_response([("call_1", "fake_tool", {"x": 1})]),
            _text_response("done."),
        ])
        self._turn(session)
        self.assertEqual(self.bus.count("fake_tool"), 1)
        self.assertFalse(self._journal_events(session)[0]["turn_failed"])
        self._script(session, [
            _tool_response([("call_2", "fake_tool", {"x": 1})]),
            _text_response("done."),
        ])
        self._turn(session)
        self.assertEqual(self.bus.count("fake_tool"), 2)

    def test_failed_tool_outcome_may_retry(self):
        """A FAILED outcome is truthfully journaled but never
        guard-blocks: retrying a failed action is legitimate."""
        self.bus = FakeBus(failures={"fail_tool"})
        cloud_text_client.tools = self.bus
        # B39 INTENTIONAL: mirror the facade re-patch in the turn
        # mixin (same dual-patch pattern as setUp; the next setUp
        # re-patches both bindings before any test body runs).
        cloud_text_turn_mod.tools = self.bus
        session = self._session()
        self._script(session, [
            _tool_response([("call_1", "fail_tool", {"x": 1})]),
            (503, {"error": {"message": "overloaded"}}),
        ])
        self._turn(session)
        events = self._journal_events(session)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "failure")
        self.assertTrue(events[0]["turn_failed"])
        self._script(session, [
            _tool_response([("call_2", "fail_tool", {"x": 1})]),
            _text_response("done."),
        ])
        self._turn(session)
        self.assertEqual(self.bus.count("fail_tool"), 2)

    def test_malformed_args_never_journaled_nor_executed(self):
        session = self._session()
        msg = session._run_tool_call({
            "id": "c1",
            "function": {"name": "fake_tool", "arguments": "{not json"},
        })
        self.assertIn("weren't valid JSON", msg["content"])
        self.assertEqual(self.bus.count("fake_tool"), 0)
        self.assertEqual(self._journal_events(session), [])


class TestJournalUnit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _path(self, name="journal.json"):
        return Path(self._tmp.name) / name

    def test_started_then_outcome(self):
        journal = GroqActionJournal(path=self._path())
        event = journal.record_started(
            tool="t", args_signature="t|{}", args={},
            turn_id="turn-1", turn_label="hello")
        self.assertEqual(event["status"], "started")
        self.assertTrue(event["invocation_id"])
        done = journal.record_outcome(
            event["invocation_id"], status="success",
            outcome_summary="ok")
        self.assertEqual(done["status"], "success")
        self.assertEqual(done["outcome_summary"], "ok")

    def test_outcome_summary_capped(self):
        journal = GroqActionJournal(path=self._path())
        event = journal.record_started(
            tool="t", args_signature="t|{}", args={},
            turn_id="turn-1")
        done = journal.record_outcome(
            event["invocation_id"], status="success",
            outcome_summary="x" * 900)
        self.assertLessEqual(len(done["outcome_summary"]), 500)

    def test_unknown_invocation_outcome_raises(self):
        journal = GroqActionJournal(path=self._path())
        with self.assertRaises(ValueError):
            journal.record_outcome("no-such-id", status="success")

    def test_sanitization_never_stores_raw_secrets(self):
        journal = GroqActionJournal(path=self._path())
        journal.record_started(
            tool="t", args_signature="t|{}",
            args={"api_key": "sk-secret-1234567890abcdef",
                  "nested": {"token": "tok_abcdef1234567890"},
                  "city": "Mumbai"},
            turn_id="turn-1")
        text = self._path().read_text(encoding="utf-8")
        self.assertNotIn("sk-secret-1234567890abcdef", text)
        self.assertNotIn("tok_abcdef1234567890", text)
        self.assertIn("[redacted]", text)
        self.assertIn("Mumbai", text)

    def test_corrupt_file_fails_loud_and_refuses_writes(self):
        path = self._path()
        path.write_text("{not json", encoding="utf-8")
        journal = GroqActionJournal(path=path)
        with self.assertRaises(ValueError):
            journal.events()
        with self.assertRaises(ValueError):
            journal.record_started(
                tool="t", args_signature="t|{}", args={},
                turn_id="turn-1")
        # The corrupt bytes are untouched, never silently rewritten.
        self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_pruning_drops_oldest_first(self):
        journal = GroqActionJournal(path=self._path(), max_events=2)
        first = journal.record_started(
            tool="a", args_signature="a|{}", args={}, turn_id="t1")
        journal.record_started(
            tool="b", args_signature="b|{}", args={}, turn_id="t2")
        journal.record_started(
            tool="c", args_signature="c|{}", args={}, turn_id="t3")
        ids = [e["invocation_id"] for e in journal.events()]
        self.assertEqual(len(ids), 2)
        self.assertNotIn(first["invocation_id"], ids)


if __name__ == "__main__":
    unittest.main()
