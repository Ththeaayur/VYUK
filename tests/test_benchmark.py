"""Benchmark subset (identity-gap P4) — fast deterministic slice of the
code-lab workflow benchmark (benchmark_workflows.py), kept inside the
tree run so the benchmark's core assertions regress with the suite.

Covers: classification, lifecycle, ledger restart/resume, C-14
through a fresh funnel registry, deep_research on a temp store, and
contract routing records. The full 78-task harness (78 machinery
tasks; the gap kind exists in the runner but no task currently
uses it) runs from ~/workspace/code-lab/benchmark_workflows.py
and is NOT duplicated here — this file pins the core, not the count.

Budget discipline follows the testiso guest rule: snapshot the
process-global budget timestamps in setUpModule, clear for a clean
window, restore byte-for-byte in tearDownModule. Confirm handler
cleared in setUp/tearDown.
"""

import tempfile
import unittest
from pathlib import Path

from core import confirm_gate
from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.brain import task_ledger as tl  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.brain.task_coordinator import StepDef  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/


def _steps(*tools):
    return [StepDef.from_mapping({"tool": t, "args": {}}) for t in tools]


def _ok(calls):
    def execute(tool, args):
        calls.append(tool)
        return {"status": "success", "result": "ok"}
    return execute


_SAVED_BUDGET = None


def setUpModule():
    global _SAVED_BUDGET
    from core.funnel import tools_handlers as th  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
    _SAVED_BUDGET = (list(getattr(th, "_budget_timestamps", [])),
                     getattr(th, "_budget_now", None))
    if hasattr(th, "reset_tool_budget"):
        th.reset_tool_budget()
    confirm_gate.clear_handler()


def tearDownModule():
    from core.funnel import tools_handlers as th  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
    stamps, clock = _SAVED_BUDGET
    if hasattr(th, "_budget_timestamps"):
        th._budget_timestamps.clear()
        th._budget_timestamps.extend(stamps)
    if clock is not None:
        th._budget_now = clock
    confirm_gate.clear_handler()


class ClassificationSubsetTests(unittest.TestCase):
    def test_plan_routine_exact(self):
        c = tc.classify_turn("run playbook Morning", plan_names=["Morning"])
        self.assertEqual(c.kind, "saved_plan")
        self.assertEqual(c.name, "Morning")
        c = tc.classify_turn("run routine Backup", routine_names=["Backup"])
        self.assertEqual(c.kind, "routine")

    def test_unresolvable_falls_through(self):
        c = tc.classify_turn("run playbook Nope", plan_names=["Morning"])
        self.assertEqual(c.kind, "single_action")

    def test_numbered_and_conversation(self):
        c = tc.classify_turn("1. open it\n2. read it\n3. close it")
        self.assertEqual(c.kind, "multi_step")
        c = tc.classify_turn("The weather is nice today")
        self.assertEqual(c.kind, "conversation")


class LifecycleSubsetTests(unittest.TestCase):
    def test_three_step_complete(self):
        coord = tc.TurnCoordinator(executor=_ok([]))
        task = coord.new_task("t", "routine", _steps("a", "b", "c"))
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.completed, 3)

    def test_cancel_mid_task(self):
        coord = tc.TurnCoordinator(executor=_ok([]))
        task = coord.new_task("t", "routine", _steps("a", "b", "c"))
        receipt = coord.run_task(
            task, on_step=lambda t, o: coord.cancel_task(t.id))
        self.assertEqual(receipt.state, "cancelled")
        self.assertEqual(receipt.remaining, ["b", "c"])

    def test_pause_resume_from_step_two(self):
        calls = []
        coord = tc.TurnCoordinator(executor=_ok(calls))
        task = coord.new_task("t", "routine", _steps("a", "b", "c"))
        first = coord.run_task(
            task, on_step=lambda t, o: coord.pause_task(t.id))
        self.assertEqual(first.state, "paused")
        second = coord.resume_task(task.id)
        self.assertEqual(second.state, "completed")
        self.assertEqual(calls, ["a", "b", "c"])


class LedgerSubsetTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "task_ledger.json"
        self.ledger = tl.TaskLedger(path=self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_restart_recovery_and_resume_skips_completed(self):
        coord = tc.TurnCoordinator(executor=_ok([]), ledger=self.ledger)
        task = coord.new_task("t", "routine", _steps("a", "b"))
        coord.run_task(task,
                       on_step=lambda t, o: coord.pause_task(t.id))
        fresh = tl.TaskLedger(path=self.path)
        record = fresh.get(task.id)
        self.assertEqual(record["state"], "paused")
        calls = []
        coord2 = tc.TurnCoordinator(executor=_ok(calls))
        receipt = coord2.resume_ledger_task(record, ledger=fresh)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(calls, ["b"])


class ConfirmGateSubsetTests(unittest.TestCase):
    def setUp(self):
        from core.funnel.tools_registry import ToolRegistry  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        confirm_gate.clear_handler()
        self._ran = []
        self._registry = ToolRegistry()
        self._registry.register(
            "send_email", "probe gated tool",
            {"type": "object", "properties": {}},
            lambda **kw: self._ran.append("send_email") or {"ok": True})

    def tearDown(self):
        confirm_gate.clear_handler()

    def test_gated_refused_without_approval(self):
        coord = tc.TurnCoordinator(executor=self._registry.execute)
        task = coord.new_task(
            "send it", "saved_plan",
            [StepDef.from_mapping({"tool": "send_email", "args": {}})])
        receipt = coord.run_task(task)
        self.assertEqual(self._ran, [])
        self.assertEqual(receipt.state, "failed")
        self.assertIn(receipt.error_kind, ("not_confirmed", "refused"))


class DeepResearchSubsetTests(unittest.TestCase):
    def test_blank_never_runs_and_temp_store_end_to_end(self):
        from core.memory import deep_research as dr  # Phase 3 M1a: moved to core/memory/
        from core.memory import knowledge  # Phase 3 M1a: moved to core/memory/
        from core.memory import research_edition_deltas  # Phase 3 M1a: moved to core/memory/
        calls = []

        def recorder(question, retrieve, max_rounds=3):
            calls.append(question)
            return {"available": True, "retrieval_log": [], "rounds_run": 0,
                    "summary": "", "citations_from_log": []}
        real = research_edition_deltas.research_loop
        research_edition_deltas.research_loop = recorder
        try:
            out = dr.run_deep_research("   ")
        finally:
            research_edition_deltas.research_loop = real
        self.assertEqual(calls, [])
        self.assertIs(out["available"], False)
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        store = knowledge.KnowledgeStore(db_path=tmp.name)
        store.add_document("doc-solar", "Solar panels",
                           "Solar panels convert sunlight into electricity.",
                           source="local-notes", licence="CC0")
        out = dr.run_deep_research("solar panels electricity", store=store)
        self.assertGreaterEqual(out["matched"], 1)
        ids = [e["doc_id"] for e in out["retrieval_log"]]
        self.assertEqual(len(ids), len(set(ids)))


class ContractRoutingSubsetTests(unittest.TestCase):
    def test_routing_records_match_contract(self):
        from core import brain_capabilities as bc
        # INTENTIONAL (voice-brain adoption): the STATIC voice
        # declaration is now True (conditional — see the
        # FreeformLoopTests). The routing record below stays False
        # because routing is the RUNTIME answer and the bridge is
        # OFF in this test env — static True vs routing False is
        # the conditionality working, not a contradiction.
        self.assertIs(bc.can_freeform_goal("gemini_live"), True)
        self.assertIs(bc.can_freeform_goal("local_text"), True)
        coord = tc.TurnCoordinator(executor=_ok([]))
        res = coord.handle_turn("step 1 open it, step 2 read it",
                                brain_id="gemini_live")
        self.assertFalse(res["handled"])
        self.assertIs(res["routing"]["live_brain_freeform_goal"], False)


if __name__ == "__main__":
    unittest.main()
