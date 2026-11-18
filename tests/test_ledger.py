"""Task Ledger + provenance + resumable checkpoints (identity-gap P2).

All ledger tests use temp dirs, never the shipped data/ dir.
The C-14 test mirrors the coordinator suite's funnel pattern
(fresh ToolRegistry + confirm_gate cleared + budget
snapshot/clear/restore per the testiso guest rule).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.brain import task_ledger as tl  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.brain.task_coordinator import StepDef, StepOutcome  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/


def _steps(*tools):
    return [StepDef.from_mapping({"tool": t, "args": {}}) for t in tools]


def _ok_executor(calls):
    def execute(tool, args):
        calls.append(tool)
        return {"status": "success", "result": "ok"}
    return execute


class LedgerWriteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "task_ledger.json"
        self.ledger = tl.TaskLedger(path=self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_record_written_per_transition_not_only_at_end(self):
        calls = []
        coord = tc.TurnCoordinator(executor=_ok_executor(calls),
                                   ledger=self.ledger)
        task = coord.new_task("t", "routine", _steps("a", "b"),
                              turn_text="run routine t", brain_id="local_text")
        # Creation alone persists a pending record.
        record = self.ledger.get(task.id)
        self.assertIsNotNone(record)
        self.assertEqual(record["state"], "pending")
        self.assertEqual(record["turn_text"], "run routine t")
        self.assertEqual(record["brain_id"], "local_text")

        def pause_after_first(task_obj, outcome):
            coord.pause_task(task_obj.id)
        coord.run_task(task, on_step=pause_after_first)
        # Mid-run state persisted: paused with exactly one outcome.
        record = self.ledger.get(task.id)
        self.assertEqual(record["state"], "paused")
        self.assertEqual(len(record["outcomes"]), 1)
        self.assertEqual(record["outcomes"][0]["tool"], "a")
        self.assertEqual(record["outcomes"][0]["status"], "success")

    def test_restart_recovery_record_intact(self):
        calls = []
        coord = tc.TurnCoordinator(executor=_ok_executor(calls),
                                   ledger=self.ledger)
        task = coord.new_task("t", "routine", _steps("a", "b"),
                              turn_text="run routine t", brain_id="local_text")

        def pause_after_first(task_obj, outcome):
            coord.pause_task(task_obj.id)
        coord.run_task(task, on_step=pause_after_first)
        # Fresh instance over the same file = app restart.
        restarted = tl.TaskLedger(path=self.path)
        record = restarted.get(task.id)
        self.assertIsNotNone(record)
        self.assertEqual(record["state"], "paused")
        self.assertEqual(record["title"], "t")
        self.assertEqual(record["classification"], "routine")
        self.assertEqual(record["turn_text"], "run routine t")
        self.assertEqual(record["brain_id"], "local_text")
        self.assertEqual(record["coordinator"], "task_coordinator/1")
        self.assertEqual(len(record["steps"]), 2)
        self.assertEqual(len(record["outcomes"]), 1)
        self.assertTrue(record["created_at"])
        self.assertTrue(record["updated_at"])

    def test_created_at_preserved_across_upserts(self):
        coord = tc.TurnCoordinator(executor=_ok_executor([]),
                                   ledger=self.ledger)
        task = coord.new_task("t", "routine", _steps("a"))
        first = self.ledger.get(task.id)["created_at"]
        coord.run_task(task)
        second = self.ledger.get(task.id)
        self.assertEqual(second["created_at"], first)
        self.assertEqual(second["state"], "completed")

    def test_interrupted_lists_correctly(self):
        calls = []
        coord = tc.TurnCoordinator(executor=_ok_executor(calls),
                                   ledger=self.ledger)
        done = coord.new_task("done", "routine", _steps("a"))
        coord.run_task(done)
        paused = coord.new_task("paused", "routine", _steps("a", "b", "c"))

        def pause_after_first(task_obj, outcome):
            coord.pause_task(task_obj.id)
        coord.run_task(paused, on_step=pause_after_first)
        summaries = self.ledger.list_interrupted()
        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual(summary["task_id"], paused.id)
        self.assertEqual(summary["state"], "paused")
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["remaining_labels"], ["b", "c"])

    def test_resume_skips_completed_steps(self):
        calls = []
        coord = tc.TurnCoordinator(executor=_ok_executor(calls),
                                   ledger=self.ledger)
        task = coord.new_task("t", "routine", _steps("a", "b", "c"))

        def pause_after_first(task_obj, outcome):
            coord.pause_task(task_obj.id)
        coord.run_task(task, on_step=pause_after_first)
        self.assertEqual(calls, ["a"])
        # Restart shape: fresh coordinator + fresh ledger instance.
        restarted = tl.TaskLedger(path=self.path)
        coord2 = tc.TurnCoordinator(executor=_ok_executor(calls))
        calls.clear()
        receipt = coord2.resume_ledger_task(restarted.get(task.id),
                                            ledger=restarted)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(calls, ["b", "c"])
        self.assertEqual(restarted.get(task.id)["state"], "completed")
        self.assertEqual(restarted.list_interrupted(), [])

    def test_unknown_outcome_resumes_as_not_completed(self):
        coord = tc.TurnCoordinator(executor=_ok_executor([]),
                                   ledger=self.ledger)
        task = coord.new_task("u", "routine", _steps("a", "b"))
        # Simulate a crash that left an unknown outcome: step a
        # succeeded, step b's outcome is unknown (never confirmed).
        task.outcomes = [
            StepOutcome("a", "a", "success", "", "ok"),
            StepOutcome("b", "b", "unknown", "timeout", "outcome lost"),
        ]
        task.state = "running"
        self.ledger.upsert_task(task)
        summary = self.ledger.list_interrupted()[0]
        self.assertEqual(summary["remaining_labels"], ["b"])
        calls = []
        coord2 = tc.TurnCoordinator(executor=_ok_executor(calls))
        receipt = coord2.resume_ledger_task(self.ledger.get(task.id),
                                            ledger=self.ledger)
        self.assertEqual(receipt.state, "completed")
        # Only b re-ran: a was never re-executed, b was re-decided.
        self.assertEqual(calls, ["b"])

    def test_terminal_record_not_resumable(self):
        coord = tc.TurnCoordinator(executor=_ok_executor([]),
                                   ledger=self.ledger)
        task = coord.new_task("t", "routine", _steps("a"))
        coord.run_task(task)
        record = self.ledger.get(task.id)
        self.assertEqual(record["state"], "completed")
        with self.assertRaises(ValueError):
            coord.resume_ledger_task(record, ledger=self.ledger)

    def test_redaction_of_outputs_and_args(self):
        secret = "sk_live_abcdefghij1234567890abcdefghij"
        calls = []

        def leaking_executor(tool, args):
            calls.append(tool)
            return {"status": "success",
                    "result": f"token is {secret} done"}
        coord = tc.TurnCoordinator(executor=leaking_executor,
                                   ledger=self.ledger)
        task = coord.new_task(
            "t", "routine",
            [StepDef.from_mapping(
                {"tool": "a",
                 "args": {"api_key": secret, "query": "hello"}})])
        coord.run_task(task)
        record = self.ledger.get(task.id)
        blob = json.dumps(record)
        self.assertNotIn(secret, blob)
        self.assertIn("[redacted]", blob)
        # The sensitive-keyed arg is redacted, the plain arg survives.
        self.assertEqual(record["steps"][0]["args"]["api_key"], "[redacted]")
        self.assertEqual(record["steps"][0]["args"]["query"], "hello")

    def test_corrupt_file_refuses_writes(self):
        self.path.write_text("{not json", encoding="utf-8")
        ledger = tl.TaskLedger(path=self.path)
        with self.assertRaises(ValueError):
            ledger.all_records()
        coord = tc.TurnCoordinator(executor=_ok_executor([]), ledger=ledger)
        task = coord.new_task("t", "routine", _steps("a"))
        coord.run_task(task)
        # The corrupt bytes are untouched: no silent overwrite.
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{not json")
        self.assertTrue(ledger.load_error)

    def test_cap_prunes_terminal_never_interrupted(self):
        coord = tc.TurnCoordinator(executor=_ok_executor([]),
                                   ledger=self.ledger)
        for i in range(205):
            task = coord.new_task(f"t{i}", "routine", _steps("a"))
            coord.run_task(task)
        paused = coord.new_task("keep", "routine", _steps("a", "b"))

        def pause_after_first(task_obj, outcome):
            coord.pause_task(task_obj.id)
        coord.run_task(paused, on_step=pause_after_first)
        records = self.ledger.all_records()
        self.assertEqual(len(records), 200)
        self.assertIsNotNone(self.ledger.get(paused.id))

    def test_remaining_all_reads_gate(self):
        # Canonical local read vs canonical network read.
        self.assertTrue(tl.remaining_all_reads(
            [{"tool": "get_current_time_date"}]))
        self.assertFalse(tl.remaining_all_reads(
            [{"tool": "get_weather"}]))
        self.assertFalse(tl.remaining_all_reads(
            [{"tool": "get_current_time_date"}, {"tool": "get_weather"}]))
        self.assertFalse(tl.remaining_all_reads(
            [{"tool": "no_such_tool_xyz"}]))
        self.assertFalse(tl.remaining_all_reads([]))


class SessionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "task_ledger.json"
        self.ledger = tl.TaskLedger(path=self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_list_interrupted_via_session(self):
        calls = []
        coord = tc.TurnCoordinator(executor=_ok_executor(calls),
                                   ledger=self.ledger)
        task = coord.new_task("t", "routine", _steps("a", "b"))

        def pause_after_first(task_obj, outcome):
            coord.pause_task(task_obj.id)
        coord.run_task(task, on_step=pause_after_first)

        class _Session:
            pass
        session = _Session()
        session._task_coordinator = coord
        summaries = tc.list_interrupted_tasks(session=session)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["task_id"], task.id)
        self.assertEqual(summaries[0]["remaining_labels"], ["b"])

    def test_list_interrupted_never_breaks(self):
        # No session, no ledger, no file: an empty list, not an error.
        self.assertEqual(tc.list_interrupted_tasks(session=object()), [])


class RegatingTests(unittest.TestCase):
    """C-14 across a restart: a prior approval never carries over."""

    def setUp(self):
        from core import confirm_gate
        from core.funnel import tools_handlers as tools_handlers_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        from core.funnel.tools_registry import ToolRegistry  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        self._confirm_gate = confirm_gate
        self._tools_handlers_mod = tools_handlers_mod
        confirm_gate.clear_handler()
        with tools_handlers_mod._budget_lock:
            self._saved_budget = list(tools_handlers_mod._budget_timestamps)
            tools_handlers_mod._budget_timestamps.clear()
        self._registry = ToolRegistry()
        self._ran = []
        self._registry.register(
            "probe_read", "probe read tool",
            {"type": "object", "properties": {}},
            lambda **kw: self._ran.append("probe_read") or {"ok": True})
        self._registry.register(
            "send_email", "probe gated tool",
            {"type": "object", "properties": {}},
            lambda **kw: self._ran.append("send_email") or {"ok": True})
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "task_ledger.json"

    def tearDown(self):
        self._confirm_gate.clear_handler()
        with self._tools_handlers_mod._budget_lock:
            self._tools_handlers_mod._budget_timestamps[:] = self._saved_budget
        self._tmp.cleanup()

    def test_gated_step_reasks_after_restart(self):
        ledger = tl.TaskLedger(path=self.path)
        coord = tc.TurnCoordinator(executor=self._registry.execute,
                                   ledger=ledger)
        task = coord.new_task(
            "t", "routine",
            [StepDef.from_mapping({"tool": "probe_read", "args": {}}),
             StepDef.from_mapping({"tool": "send_email", "args": {}})])

        def pause_after_first(task_obj, outcome):
            coord.pause_task(task_obj.id)
        coord.run_task(task, on_step=pause_after_first)
        self.assertEqual(self._ran, ["probe_read"])
        # Restart shape: fresh coordinator + fresh ledger, NO approval.
        restarted = tl.TaskLedger(path=self.path)
        coord2 = tc.TurnCoordinator(executor=self._registry.execute)
        self._ran.clear()
        receipt = coord2.resume_ledger_task(restarted.get(task.id),
                                            ledger=restarted)
        self.assertEqual(receipt.state, "failed")
        self.assertEqual(self._ran, [])
        self.assertIn(receipt.error_kind, ("not_confirmed", "refused"))


if __name__ == "__main__":
    unittest.main()
