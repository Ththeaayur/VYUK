"""Audit fix A5 — execution (master audit P1-3 + P1-10, wiring
audit WIR-04 + WIR-05).

Timeouts: a timed-out call used to be abandoned with only a log
line and an error dict claiming nothing. Now it gets a durable
operation id and an explicit outcome-unknown result, and the
abandoned worker's late outcome finalizes that operation exactly
once. No layer auto-retries an unknown outcome: the checkpoint
adapter propagates "unknown" and the reflection retry refuses it.

Finalization race: the old boolean bracket handoff could strand
the taint bracket begun-but-never-ended when a handler finished
in the join window. core.injection.ExecutionBracket resolves
every interleaving to exactly one ender, unit-tested here with
barrier-synchronized threads, and proven through the funnel by
the in-flight counter (held while the worker runs, drained when
it lands).

Structured results: the funnel boundary normalizes every result
to success / failure / refused / timeout / unknown. A handler
returning a funnel-shaped dict surfaces as that outcome, never
re-wrapped as success; a handler returning a refusal sentence
becomes a refused result carrying the sentence. Consumers that
branched on legacy "error" accept both vocabularies.

Batches: the Live batch executor used to pre-launch every
read-only call, so a later read could observe pre-write state,
and one raising call aborted the whole batch response. Now a
batch with any write runs in issue order (all-read batches
still overlap), each call's unexpected exception becomes a
stable internal_error result for that call only, cancellation
still propagates, and a callback exception cannot kill a batch.

Stdlib unittest only. Run via tests/run_tests.py with
PYTHONPATH=. (the suites need it). Funnel tests use a fresh
ToolRegistry with uniquely-named probe tools and a handful of
calls, so they do not disturb the full run's shared budget.
"""

import asyncio
import json
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import injection
from core import tool_receipts as receipts_mod
from core.brain import planner as planner_mod  # Phase 3 B2-remainder INTENTIONAL: planner moved to core/brain/
from core.brain import reflection  # Phase 3 B1a-1 INTENTIONAL: moved to core/brain/
from core.funnel import tool_operations as ops_mod  # Phase 3 F2a INTENTIONAL: tool_operations moved to core/funnel/
from core.funnel import tool_result as tr_mod  # Phase 3 F2a INTENTIONAL: tool_result moved to core/funnel/
from core.funnel import tools_registry as registry_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel import tools_registry_exec_dispatch as dispatch_mod  # INTENTIONAL (tools_registry batch 2): limits/deadline stage binding for dual-patches below


def _wait_until(predicate, timeout_s=5.0, tick=0.05):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(tick)
    return predicate()


class ContractTests(unittest.TestCase):
    def test_vocabulary_and_legacy(self):
        # P-2 (debug pass): the outcome vocabulary gained
        # 'cancelled' (a cancelled task's outcome) — the pin
        # follows the live vocabulary.
        self.assertEqual(
            tr_mod.STATUSES,
            ("success", "failure", "refused", "timeout", "unknown",
             "cancelled"))
        for status in ("failure", "refused", "timeout", "unknown",
                       "error"):
            self.assertTrue(tr_mod.is_non_success({"status": status}))
        self.assertFalse(tr_mod.is_non_success({"status": "success"}))
        self.assertFalse(tr_mod.is_non_success({"status": "ok"}))
        self.assertFalse(tr_mod.is_non_success("not a dict"))

    def test_normalize_maps_legacy_error_by_kind(self):
        self.assertEqual(
            tr_mod.normalize_result(
                {"status": "error", "kind": "refused",
                 "message": "m"})["status"], "refused")
        self.assertEqual(
            tr_mod.normalize_result(
                {"status": "error", "kind": "not_confirmed",
                 "message": "m"})["status"], "refused")
        self.assertEqual(
            tr_mod.normalize_result(
                {"status": "error", "kind": "validation",
                 "message": "m"})["status"], "refused")
        self.assertEqual(
            tr_mod.normalize_result(
                {"status": "error", "kind": "budget",
                 "message": "m"})["status"], "refused")
        # Cool-down timeouts are refusals; genuine timeouts pass
        # status "timeout" directly and never reach this mapping.
        self.assertEqual(
            tr_mod.normalize_result(
                {"status": "error", "kind": "timeout",
                 "message": "m"})["status"], "refused")
        self.assertEqual(
            tr_mod.normalize_result(
                {"status": "error", "kind": "handler_error",
                 "message": "m"})["status"], "failure")
        self.assertEqual(
            tr_mod.normalize_result(
                {"status": "error", "kind": "unavailable",
                 "message": "m"})["status"], "failure")
        self.assertEqual(
            tr_mod.normalize_result(
                {"status": "success", "result": 1})["status"], "success")
        self.assertEqual(tr_mod.normalize_result("plain"), "plain")

    def test_funnel_shaped_detection(self):
        self.assertTrue(tr_mod.is_funnel_shaped(
            {"status": "error", "kind": "handler_error",
             "message": "boom"}))
        self.assertTrue(tr_mod.is_funnel_shaped(
            {"status": "failure", "message": "boom"}))
        # A data dict that merely carries a "status" key is NOT
        # funnel-shaped and must stay a success payload.
        self.assertFalse(tr_mod.is_funnel_shaped(
            {"status": "error"}))
        self.assertFalse(tr_mod.is_funnel_shaped(
            {"status": "ok", "data": [1, 2]}))
        self.assertFalse(tr_mod.is_funnel_shaped(
            {"status": "success", "result": 1}))
        self.assertFalse(tr_mod.is_funnel_shaped("text"))

    def test_refusal_markers(self):
        self.assertTrue(tr_mod.is_refusal_text(
            "Refused: that needs your approval first."))
        self.assertTrue(tr_mod.is_refusal_text(
            "Blocked by VYUK's trust layer"))
        self.assertFalse(tr_mod.is_refusal_text(
            "Done: deleted the file"))
        self.assertFalse(tr_mod.is_refusal_text(42))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        ops_mod.set_store({})

    def tearDown(self):
        ops_mod.reset_store()

    def test_record_finalize_exactly_once(self):
        op = ops_mod.record_timeout("probe_tool", "funnel")
        self.assertTrue(op)
        pending = ops_mod.pending_operations()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["op_id"], op)
        self.assertEqual(pending[0]["status"], "unknown")
        self.assertFalse(ops_mod.is_reconciled(op))
        self.assertTrue(ops_mod.finalize_operation(
            op, {"status": "completed"}))
        self.assertFalse(ops_mod.finalize_operation(
            op, {"status": "failed"}))
        self.assertEqual(ops_mod.pending_operations(), [])
        record = ops_mod.get_operation(op)
        self.assertEqual(record["status"], "completed")
        self.assertTrue(ops_mod.is_reconciled(op))

    def test_finalize_unknown_id_is_false(self):
        self.assertFalse(ops_mod.finalize_operation(
            "no-such-op", {"status": "completed"}))
        self.assertIsNone(ops_mod.get_operation("no-such-op"))
        self.assertTrue(ops_mod.is_reconciled("no-such-op"))

    def test_finalize_keeps_only_status_and_short_note(self):
        op = ops_mod.record_timeout("probe_tool", "live_batch")
        self.assertTrue(ops_mod.finalize_operation(
            op, {"status": "failed", "error": "RuntimeError",
                 "secret_payload": "must not persist"}))
        record = ops_mod.get_operation(op)
        self.assertEqual(record["final"]["status"], "failed")
        self.assertEqual(record["final"]["note"], "RuntimeError")
        self.assertNotIn("secret_payload", record["final"])


class BracketProtocolTests(unittest.TestCase):
    def test_sequential_paths(self):
        # Success: worker finishes first, caller ends.
        bracket = injection.ExecutionBracket()
        self.assertFalse(bracket.worker_finished())
        self.assertTrue(bracket.caller_finished())
        self.assertFalse(bracket.caller_finished())
        # No-deadline: caller ends directly.
        bracket = injection.ExecutionBracket()
        self.assertTrue(bracket.caller_finished())
        self.assertFalse(bracket.caller_finished())
        # Timeout, worker still running: worker ends later.
        bracket = injection.ExecutionBracket()
        ends_now, worker_owns = bracket.caller_timed_out()
        self.assertFalse(ends_now)
        self.assertTrue(worker_owns)
        self.assertFalse(bracket.caller_finished())
        self.assertTrue(bracket.worker_finished())
        self.assertFalse(bracket.worker_finished())

    def test_race_window_resolves_to_exactly_one_ender(self):
        # Barrier-synchronized: the worker's finally and the
        # caller's timeout decision race N times; every
        # interleaving must yield exactly one ender.
        for _ in range(200):
            bracket = injection.ExecutionBracket()
            barrier = threading.Barrier(2)
            outcomes: dict[str, object] = {}

            def worker():
                barrier.wait(timeout=5)
                outcomes["worker"] = bracket.worker_finished()

            def caller():
                barrier.wait(timeout=5)
                outcomes["caller"] = bracket.caller_timed_out()

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=caller)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)
            worker_ends = outcomes["worker"]
            caller_ends, worker_owns = outcomes["caller"]
            enders = (1 if worker_ends else 0) \
                + (1 if caller_ends else 0)
            self.assertEqual(enders, 1,
                             f"race resolved to {enders} enders")
            self.assertEqual(worker_owns, not caller_ends)
            self.assertFalse(bracket.caller_finished())


class FunnelContractTests(unittest.TestCase):
    def setUp(self):
        ops_mod.set_store({})
        # Budget isolation (the A4 lesson, applied here
        # first): the executor's safety budget is a
        # PROCESS-GLOBAL 60-second counter, and this suite's
        # real funnel executes would otherwise consume it
        # and trip unrelated later suites in a full run
        # (proven: the full run without this file is
        # baseline-identical, with it the release_spine
        # budget tests trip). The budget itself is covered
        # by the A2 suite, not here — so these executes run
        # with the brake patched to pass, and consume
        # nothing. No other funnel behavior is stubbed.
        # INTENTIONAL (tools_registry batch 2, B16/B47 pattern):
        # the limits stage reads _budget_check from the dispatch
        # mixin module, not only from the facade -- patch both
        # bindings with the same fake. Same fake, same
        # assertions; no assertion weakened.
        self._budget_patcher = mock.patch.object(
            registry_mod, "_budget_check",
            lambda name, is_read: None)
        self._budget_patcher2 = mock.patch.object(
            dispatch_mod, "_budget_check",
            lambda name, is_read: None)
        self._budget_patcher.start()
        self._budget_patcher2.start()

    def tearDown(self):
        self._budget_patcher2.stop()
        self._budget_patcher.stop()
        ops_mod.reset_store()

    def _registry(self):
        return registry_mod.ToolRegistry()

    def test_unknown_tool_is_failure_not_legacy_error(self):
        reg = self._registry()
        result = reg.execute("a5_no_such_tool", {})
        self.assertEqual(result.get("status"), "failure")
        self.assertEqual(result.get("kind"), "unavailable")

    def test_validation_is_refused(self):
        reg = self._registry()
        reg.register(
            "a5_probe_validated", "probe",
            {"type": "object",
             "properties": {"x": {"type": "string"}},
             "required": ["x"]},
            lambda **kw: {"ok": True})
        result = reg.execute("a5_probe_validated", {})
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "validation")

    def test_handler_raises_is_failure(self):
        reg = self._registry()

        def _boom(**kw):
            raise ValueError("boom")

        reg.register("a5_probe_raises", "probe",
                     {"type": "object", "properties": {}}, _boom)
        result = reg.execute("a5_probe_raises", {})
        self.assertEqual(result.get("status"), "failure")
        self.assertEqual(result.get("kind"), "handler_error")
        self.assertIn("Execution failed", result.get("message", ""))

    def test_refusal_sentence_becomes_refused_not_success(self):
        reg = self._registry()
        reg.register(
            "a5_probe_refusal", "probe",
            {"type": "object", "properties": {}},
            lambda **kw: "Refused: that needs your approval first.")
        result = reg.execute("a5_probe_refusal", {})
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "refused")
        self.assertIn("Refused:", result.get("message", ""))

    def test_nested_error_dict_surfaces_not_success(self):
        reg = self._registry()
        reg.register(
            "a5_probe_nested", "probe",
            {"type": "object", "properties": {}},
            lambda **kw: {"status": "error",
                          "kind": "handler_error",
                          "message": "inner boom"})
        result = reg.execute("a5_probe_nested", {})
        self.assertEqual(result.get("status"), "failure")
        self.assertEqual(result.get("kind"), "handler_error")
        # The H-1 failure script appends its next-step line to
        # the message; the inner sentence must survive verbatim
        # at the front, never re-wrapped as success.
        self.assertTrue(result.get("message", "").startswith(
            "inner boom"))

    def test_data_dict_with_status_key_stays_success_payload(self):
        reg = self._registry()
        reg.register(
            "a5_probe_data", "probe",
            {"type": "object", "properties": {}},
            lambda **kw: {"status": "ok", "data": [1, 2]})
        result = reg.execute("a5_probe_data", {})
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("result"),
                         {"status": "ok", "data": [1, 2]})

    def test_producer_content_refusal_phrase_stays_data(self):
        # Regression for a real A5 overreach found by the full
        # run: "read_file" is an untrusted producer, so a file
        # whose text begins "Refused: no" is DATA, not a
        # verdict — it must still arrive envelope-wrapped as a
        # success, never converted into a refused result.
        reg = registry_mod.ToolRegistry()
        reg.register(
            "read_file", "probe",
            {"type": "object", "properties": {}},
            lambda: "Refused: no")
        try:
            result = reg.execute("read_file", {})
        finally:
            # The wrapped content hard-taints the session; clear
            # it so later tests in this process run untainted
            # (the A4 isolation lesson, applied here first).
            injection.clear_taint(force=True)
        self.assertEqual(result.get("status"), "success")
        body = str(result.get("result"))
        self.assertTrue(body.startswith("[Untrusted file content"))
        self.assertIn("Refused: no", body)

    def test_timeout_unknown_outcome_and_late_finalize(self):
        reg = self._registry()
        finished: list[str] = []
        # P-8 (debug pass): the probe name is unique per run. The
        # funnel's identical-call dedup guard refuses a repeat of a
        # call whose previous outcome is unknown — and this test
        # deliberately times out (0.15 s deadline, 0.4 s probe), so
        # a fixed name poisoned every later run against the same
        # data/: the full-suite failures were dedup refusals
        # ("previous identical call ... has an unknown outcome"),
        # not dispatch overhead. Proven: the test passes on fresh
        # data/ and fails 'refused' on a second run against the
        # same data/. No assertion names the tool, so a unique
        # name changes nothing the test checks.
        probe_name = "a5_probe_timeout_%s" % uuid.uuid4().hex[:8]

        def _slow(**kw):
            time.sleep(0.4)
            finished.append("done")
            return {"ok": True}

        reg.register(probe_name, "probe",
                     {"type": "object", "properties": {}}, _slow)
        baseline = injection.in_flight()
        # INTENTIONAL (tools_registry batch 2, B16/B47 pattern):
        # the deadline-legs stage reads deadline_for from the
        # dispatch mixin module, not only from the facade --
        # patch both bindings with the same fake.
        # The 0.15 s deadline (was 0.05 s) still fires the timeout
        # (the probe sleeps 0.4 s) with headroom for Qt dispatch
        # jitter under load.
        with mock.patch.object(
                registry_mod, "deadline_for",
                lambda name, args=None: 0.15), mock.patch.object(
                dispatch_mod, "deadline_for",
                lambda name, args=None: 0.15):
            result = reg.execute(probe_name, {})
        self.assertEqual(result.get("status"), "timeout")
        self.assertEqual(result.get("kind"), "timeout")
        self.assertEqual(result.get("outcome"), "unknown")
        op_id = result.get("operation_id")
        self.assertTrue(op_id)
        self.assertIn("Do not automatically retry",
                      result.get("message", ""))
        # The bracket is still held by the abandoned worker.
        self.assertEqual(injection.in_flight(), baseline + 1)
        # The worker lands, the bracket drains exactly once, and
        # the operation finalizes exactly once as completed.
        self.assertTrue(_wait_until(
            lambda: injection.in_flight() == baseline, timeout_s=5))
        self.assertTrue(_wait_until(
            lambda: ops_mod.is_reconciled(op_id), timeout_s=5))
        record = ops_mod.get_operation(op_id)
        self.assertEqual(record["status"], "completed")
        self.assertEqual(finished, ["done"])

    def test_timeout_result_carries_no_retry_offer(self):
        from core.funnel import failure_script as script_mod  # Phase 3 F2b INTENTIONAL: failure_script moved to core/funnel/

        timed_out = {"status": "timeout", "kind": "timeout",
                     "outcome": "unknown", "operation_id": "abc",
                     "message": "timed out"}
        out = script_mod.apply_failure_script(
            "probe_tool", {}, timed_out,
            is_read=False, known=True)
        self.assertNotIn("failure", out)
        self.assertEqual(out["status"], "timeout")


class NoRetryTests(unittest.TestCase):
    def test_unknown_outcome_detection(self):
        self.assertTrue(planner_mod._is_unknown_outcome(
            {"status": "timeout", "kind": "timeout"}))
        self.assertTrue(planner_mod._is_unknown_outcome(
            {"status": "failure", "outcome": "unknown"}))
        self.assertFalse(planner_mod._is_unknown_outcome(
            {"status": "success", "result": "ok"}))
        self.assertFalse(planner_mod._is_unknown_outcome(
            {"status": "failure", "kind": "handler_error"}))

    def test_reflection_never_retries_unknown(self):
        calls: list[dict] = []

        def _executor(step):
            calls.append(step)
            return {"ok": False, "unknown": True,
                    "error": "timed out"}

        report = reflection.reflect_and_retry(
            {"tool": "probe"}, _executor, lambda text: "think again")
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["retries_used"], 0)
        self.assertFalse(report["reflected"])

    def test_reflection_still_retries_plain_failure(self):
        calls: list[dict] = []

        def _executor(step):
            calls.append(step)
            if len(calls) == 1:
                return {"ok": False, "error": "first failed"}
            return {"ok": True, "result": "second ok"}

        report = reflection.reflect_and_retry(
            {"tool": "probe"}, _executor, lambda text: "think again")
        self.assertEqual(len(calls), 2)
        self.assertEqual(report["retries_used"], 1)
        self.assertEqual(report["outcome"]["ok"], True)


class _FakeFunctionResponse:
    def __init__(self, name=None, id=None, response=None):
        self.name = name
        self.id = id
        self.response = response


class _FakeTypes:
    FunctionResponse = _FakeFunctionResponse


class BatchTests(unittest.TestCase):
    def setUp(self):
        ops_mod.set_store({})
        from core.voice import live_client as live_mod  # Phase 3 V1b INTENTIONAL: moved to core/voice/
        from core.voice import live_client_receive as live_recv_mod  # B47 INTENTIONAL: receive mixin binding for dual patches

        self.live_mod = live_mod
        self._patches = [
            mock.patch.object(live_mod, "deadline_for",
                              lambda name, args=None: None),
            mock.patch.object(live_mod, "timeout_cooldown_active",
                              lambda name, args=None: None),
            mock.patch.object(live_mod, "note_tool_timeout",
                              lambda name, args=None: None),
            mock.patch.object(live_mod, "types", _FakeTypes),
            # INTENTIONAL (skills split B47): _run_tool_batch moved
            # to the receive mixin and reads all four from its own
            # globals — patch both bindings (B16 pattern, same
            # fakes, both restored via the same list).
            mock.patch.object(live_recv_mod, "deadline_for",
                              lambda name, args=None: None),
            mock.patch.object(live_recv_mod, "timeout_cooldown_active",
                              lambda name, args=None: None),
            mock.patch.object(live_recv_mod, "note_tool_timeout",
                              lambda name, args=None: None),
            mock.patch.object(live_recv_mod, "types", _FakeTypes),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self._patches):
            patch.stop()
        ops_mod.reset_store()

    def _session(self, execute_fn, on_tool_call=None):
        session = object.__new__(self.live_mod.GeminiLiveSession)
        session._running = True
        session._cancelled_call_ids = set()
        session.on_tool_call = on_tool_call
        session.on_transcript = None
        session._execute_tool_sync = execute_fn
        delivered: list = []

        async def _deliver(responses):
            delivered.extend(responses)

        session._deliver_tool_responses = _deliver
        session._delivered = delivered
        return session

    def _call(self, name, call_id, args=None):
        return SimpleNamespace(name=name, id=call_id,
                               args=args or {})

    def _run(self, session, batch):
        return asyncio.run(session._run_tool_batch(batch))

    def test_write_then_read_runs_in_issue_order(self):
        events: list[tuple[str, float, float]] = []

        def _execute(fc):
            start = time.monotonic()
            if fc.name == "a5_probe_write":
                time.sleep(0.15)
            end = time.monotonic()
            events.append((fc.name, start, end))
            return {"status": "success", "result": "ok"}

        session = self._session(_execute)
        batch = [self._call("a5_probe_write", "c1"),
                 self._call("get_current_time_date", "c2")]
        self._run(session, batch)
        by_name = {name: (start, end) for name, start, end in events}
        self.assertEqual([name for name, _, _ in events],
                         ["a5_probe_write", "get_current_time_date"])
        # Serialized: the read starts only after the write ends.
        self.assertGreaterEqual(by_name["get_current_time_date"][0],
                                by_name["a5_probe_write"][1])
        self.assertEqual(len(session._delivered), 2)

    def test_all_read_batch_still_overlaps(self):
        events: list[tuple[str, float, float]] = []

        def _execute(fc):
            start = time.monotonic()
            time.sleep(0.2)
            end = time.monotonic()
            events.append((fc.name, start, end))
            return {"status": "success", "result": "ok"}

        session = self._session(_execute)
        batch = [self._call("get_current_time_date", "c1"),
                 self._call("get_plan", "c2")]
        self._run(session, batch)
        by_name = {name: (start, end) for name, start, end in events}
        # Concurrent: the second read starts before the first ends.
        self.assertLess(by_name["get_plan"][0],
                        by_name["get_current_time_date"][1])
        self.assertEqual(len(session._delivered), 2)

    def test_one_raising_sibling_still_delivers_all(self):
        def _execute(fc):
            if fc.name == "a5_probe_boom":
                raise RuntimeError("kaboom")
            return {"status": "success", "result": "ok"}

        session = self._session(_execute)
        batch = [self._call("a5_probe_write", "c1"),
                 self._call("a5_probe_boom", "c2"),
                 self._call("get_current_time_date", "c3")]
        self._run(session, batch)
        delivered = session._delivered
        self.assertEqual(len(delivered), 3)
        by_id = {resp.id: resp.response["result"]
                 for resp in delivered}
        self.assertEqual(by_id["c1"]["status"], "success")
        self.assertEqual(by_id["c2"]["status"], "failure")
        self.assertEqual(by_id["c2"]["kind"], "internal_error")
        self.assertNotIn("kaboom", by_id["c2"].get("message", ""))
        self.assertEqual(by_id["c3"]["status"], "success")

    def test_callback_exception_cannot_kill_batch(self):
        def _on_tool_call(name, args, result):
            raise RuntimeError("callback blew up")

        def _execute(fc):
            return {"status": "success", "result": "ok"}

        session = self._session(_execute, on_tool_call=_on_tool_call)
        batch = [self._call("a5_probe_write", "c1"),
                 self._call("get_current_time_date", "c2")]
        self._run(session, batch)
        self.assertEqual(len(session._delivered), 2)

    def test_cancelled_call_gets_no_response(self):
        def _execute(fc):
            return {"status": "success", "result": "ok"}

        session = self._session(_execute)
        session._cancelled_call_ids.add("c2")
        batch = [self._call("a5_probe_write", "c1"),
                 self._call("get_current_time_date", "c2"),
                 self._call("get_plan", "c3")]
        self._run(session, batch)
        ids = [resp.id for resp in session._delivered]
        self.assertEqual(ids, ["c1", "c3"])

    def test_cancellation_propagates_not_result(self):
        def _execute(fc):
            raise asyncio.CancelledError()

        session = self._session(_execute)
        batch = [self._call("a5_probe_write", "c1")]
        with self.assertRaises(asyncio.CancelledError):
            self._run(session, batch)

    def test_batch_timeout_unknown_and_late_finalize(self):
        from core.voice import live_client_receive as live_recv_mod  # B47 INTENTIONAL: receive mixin binding for dual patch

        def _execute(fc):
            time.sleep(0.3)
            return {"status": "success", "result": "late ok"}

        session = self._session(_execute)
        # INTENTIONAL (skills split B47): _run_tool_batch moved to
        # the receive mixin and reads deadline_for from its own
        # globals — patch both bindings (B16 pattern, same fake).
        with mock.patch.object(
                self.live_mod, "deadline_for",
                lambda name, args=None: 0.05), mock.patch.object(
                live_recv_mod, "deadline_for",
                lambda name, args=None: 0.05):
            self._run(session, [self._call("a5_probe_slow", "c1")])
        delivered = session._delivered
        self.assertEqual(len(delivered), 1)
        result = delivered[0].response["result"]
        self.assertEqual(result.get("status"), "timeout")
        self.assertEqual(result.get("outcome"), "unknown")
        op_id = result.get("operation_id")
        self.assertTrue(op_id)
        self.assertIn("Do not automatically retry",
                      result.get("message", ""))
        # The abandoned call lands later and finalizes the op.
        self.assertTrue(_wait_until(
            lambda: ops_mod.is_reconciled(op_id), timeout_s=5))
        record = ops_mod.get_operation(op_id)
        self.assertEqual(record["status"], "success")

    def test_delivery_failure_retries_then_surfaces_loss_note(self):
        # Field-check fix #2 (T5): the tools already ran, but
        # the model never got their results — one retry, then a
        # receipt note + a chat line, and no raise out of the
        # batch (the worker loop must not swallow it as a bare
        # print). The real _deliver_tool_responses runs here —
        # the _session helper's recording fake would hide the
        # failure, so this test builds its session by hand.
        calls = {"n": 0}

        class _RaisingSession:
            async def send_tool_response(self, function_responses=None,
                                         **kw):
                calls["n"] += 1
                raise RuntimeError("send failed")

        session = object.__new__(self.live_mod.GeminiLiveSession)
        session._running = True
        session.is_connected = True
        session._cancelled_call_ids = set()
        session.on_tool_call = None
        transcripts: list[tuple[str, str]] = []
        session.on_transcript = lambda role, text: transcripts.append(
            (role, text))
        session._session = _RaisingSession()
        session._execute_tool_sync = lambda fc: {
            "status": "success", "result": "ok"}
        self._run(session, [self._call("get_current_time_date", "c1")])
        self.assertEqual(calls["n"], 2)  # one retry, then surfaced
        self.assertTrue(any(
            "deliver" in text.lower() and "fail" in text.lower()
            for _, text in transcripts),
            f"no delivery-failure chat line in {transcripts!r}")

    def _receipt_outcomes(self, store_path):
        outcomes = []
        for line in Path(store_path).read_text(
                encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec["type"] == "outcome":
                outcomes.append(rec)
        return outcomes

    def test_cancelled_during_execution_leaves_dispatched_receipt(self):
        # Field-check fix #2 (T7 #17): a call that ran and was
        # then cancelled by the server leaves a "cancelled"
        # receipt stating the result was dropped at delivery —
        # with dispatched=True, because it DID run (the guard
        # must refuse a re-run until verified, not treat it as
        # never-run).
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store_path = Path(tmp.name) / "r.jsonl"
        receipts_mod.install_store(store_path)
        self.addCleanup(receipts_mod.reset_default)

        def _execute(fc):
            return {"status": "success", "result": "wrote it"}

        session = self._session(_execute)

        def _cancel(name, args, result):
            session._cancelled_call_ids.add("c1")

        session.on_tool_call = _cancel
        self._run(session, [self._call("write_file", "c1",
                                       {"filepath": "/tmp/x",
                                        "content": "t17"})])
        self.assertEqual(session._delivered, [])
        cancelled = [o for o in self._receipt_outcomes(store_path)
                     if o["status"] == "cancelled"]
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0]["summary"],
                         "cancelled during execution — result "
                         "dropped at delivery")
        self.assertTrue(cancelled[0]["dispatched"])

    def test_stop_mid_batch_receipts_undispatched_calls(self):
        # Field-check fix #2 (T7 #18): stopping mid-batch
        # records the calls that never dispatched — the pending
        # outcomes are dropped, but not silently. B never ran,
        # so its receipt carries dispatched=False (safe to
        # re-run); A's result is dropped with the rest.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store_path = Path(tmp.name) / "r.jsonl"
        receipts_mod.install_store(store_path)
        self.addCleanup(receipts_mod.reset_default)

        def _execute(fc):
            return {"status": "success", "result": "ok"}

        session = self._session(_execute)

        def _stop(name, args, result):
            session._running = False

        session.on_tool_call = _stop
        self._run(session, [self._call("write_file", "c1",
                                       {"filepath": "/tmp/a",
                                        "content": "A"}),
                            self._call("write_file", "c2",
                                       {"filepath": "/tmp/b",
                                        "content": "B"})])
        self.assertEqual(session._delivered, [])
        cancelled = [o for o in self._receipt_outcomes(store_path)
                     if o["status"] == "cancelled"]
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0]["summary"],
                         "batch stopped — call not delivered")
        self.assertFalse(cancelled[0]["dispatched"])


if __name__ == "__main__":
    unittest.main()
