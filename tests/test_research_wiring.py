"""Research wiring batch: the A6 research_loop now has a real
production caller — the deep_research tool (core/memory/deep_research.py,
registered in core/funnel/tools_handlers.py, grouped in Mind, routed via
route_mind). These tests prove the CALLER invokes the loop (not
merely that the loop exists), the local-only retriever, the honest
empty/failure states, the funnel leg, and the gate classification.

Budget discipline (testiso guest rule): the one funnel test
snapshots the process-global budget timestamps in setUpModule,
resets for a clean window, and restores byte-for-byte in
tearDownModule — never blanked mid-run. No other test here
executes through the funnel.
"""

import json
import tempfile
import unittest
from pathlib import Path

from core.memory import deep_research as dr  # Phase 3 M1a: moved to core/memory/
from core.memory import knowledge  # Phase 3 M1a: moved to core/memory/
from core.memory import research_edition_deltas  # Phase 3 M1a: moved to core/memory/
from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/


def _temp_store():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = knowledge.KnowledgeStore(db_path=tmp.name)
    store.add_document("doc-solar", "Solar panels",
                       "Solar panels convert sunlight into electricity.",
                       source="local-notes", licence="CC0")
    store.add_document("doc-tides", "Tidal power",
                       "Tidal power harnesses ocean tides for electricity.",
                       source="local-notes", licence="CC0")
    return store


def _set_up_module_budget():
    from core.funnel import tools_handlers as th  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
    saved = (list(getattr(th, "_budget_timestamps", [])),
             getattr(th, "_budget_now", None))
    if hasattr(th, "reset_tool_budget"):
        th.reset_tool_budget()
    return saved


def _tear_down_module_budget(saved):
    from core.funnel import tools_handlers as th  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
    stamps, clock = saved
    if hasattr(th, "_budget_timestamps"):
        th._budget_timestamps.clear()
        th._budget_timestamps.extend(stamps)
    if clock is not None:
        th._budget_now = clock


_SAVED_BUDGET = None


def setUpModule():
    global _SAVED_BUDGET
    _SAVED_BUDGET = _set_up_module_budget()


def tearDownModule():
    if _SAVED_BUDGET is not None:
        _tear_down_module_budget(_SAVED_BUDGET)


class CallerTests(unittest.TestCase):
    def test_caller_invokes_research_loop(self):
        # The caller must call research_loop itself — a
        # reimplementation would pass an end-to-end test but
        # leave the loop unwired. Observe the call directly.
        calls = []
        real = research_edition_deltas.research_loop

        def recorder(question, retrieve, max_rounds=3):
            calls.append((question, retrieve, max_rounds))
            return {"question": question, "retrieval_log": [],
                    "rounds_run": 0, "summary": "",
                    "citations_from_log": []}

        research_edition_deltas.research_loop = recorder
        try:
            out = dr.run_deep_research(
                "solar power",
                retrieve=lambda q: [{"doc_id": "x", "title": "t",
                                     "passage": "p", "source": "s",
                                     "licence": ""}])
        finally:
            research_edition_deltas.research_loop = real
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "solar power")
        self.assertTrue(callable(calls[0][1]))
        self.assertEqual(calls[0][2], 3)
        # The recorder's empty log surfaces as honest no-findings.
        self.assertEqual(out["matched"], 0)
        self.assertIn("no stored documents matched", out["note"])

    def test_blank_question_never_runs_loop(self):
        calls = []
        real = research_edition_deltas.research_loop
        research_edition_deltas.research_loop = lambda *a, **k: calls.append(1)
        try:
            out = dr.run_deep_research("   ")
        finally:
            research_edition_deltas.research_loop = real
        self.assertEqual(calls, [])
        self.assertIs(out["available"], False)
        self.assertIn("question", out["reason"])

    def test_max_rounds_capped_by_kernel(self):
        docs = [{"doc_id": "d1", "title": "t", "passage": "p",
                 "source": "s", "licence": ""}]
        out = dr.run_deep_research("q", max_rounds=10,
                                   retrieve=lambda q: list(docs))
        self.assertEqual(out["rounds_run"], 3)
        self.assertEqual(out["matched"], 3)


class LocalRetrieverTests(unittest.TestCase):
    def test_end_to_end_with_temp_store(self):
        store = _temp_store()
        out = dr.run_deep_research("solar panels", store=store)
        self.assertTrue(out.get("retrieval_log"), out)
        self.assertGreaterEqual(out["matched"], 1)
        self.assertIn("doc-solar", out["citations_from_log"])
        self.assertIn("sunlight into electricity", out["summary"])
        self.assertIs(out["executor_access"], False)
        # Each doc appears exactly once despite 3 kernel rounds.
        ids = [e["doc_id"] for e in out["retrieval_log"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_dedup_breaks_early(self):
        store = _temp_store()
        out = dr.run_deep_research("electricity", store=store,
                                   max_rounds=3)
        # Round 1 retrieves, round 2 finds nothing new -> break.
        self.assertEqual(out["rounds_run"], 2)
        ids = [e["doc_id"] for e in out["retrieval_log"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_empty_store_is_honest_no_findings(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        store = knowledge.KnowledgeStore(db_path=tmp.name)
        out = dr.run_deep_research("anything at all", store=store)
        self.assertEqual(out["matched"], 0)
        self.assertEqual(out["summary"], "")
        self.assertEqual(out["citations_from_log"], [])
        self.assertIn("no stored documents matched", out["note"])
        # No fabrication: the note says this is about the LOCAL
        # store only, never a claim about the wider literature.
        self.assertIn("local store", out["note"])

    def test_store_failure_is_honest_unavailable(self):
        class BrokenStore:
            def search(self, query, limit=10):
                raise OSError("disk gone")

        out = dr.run_deep_research("solar", store=BrokenStore())
        self.assertIs(out["available"], False)
        self.assertIn("knowledge store unavailable", out["reason"])


class FunnelAndGateTests(unittest.TestCase):
    def test_funnel_execute_reaches_loop(self):
        store = _temp_store()
        real_cls = knowledge.KnowledgeStore
        knowledge.KnowledgeStore = lambda: store
        try:
            result = tools.execute(
                "deep_research", {"question": "solar panels"})
        finally:
            knowledge.KnowledgeStore = real_cls
        self.assertEqual(result.get("status"), "success", result)
        payload = json.loads(result["result"])
        self.assertGreaterEqual(payload["matched"], 1)
        self.assertIn("doc-solar", payload["citations_from_log"])

    def test_gate_classification_is_local_read(self):
        self.assertIn("deep_research", gate._READ_ONLY_TOOLS)
        self.assertIn("deep_research", gate._LOCAL_ONLY_READS)
        # Local-only is load-bearing: _is_egress_read returns True
        # for any read-only tool OUTSIDE _LOCAL_ONLY_READS, which
        # would refuse this purely local read at rung 2.
        self.assertFalse(gate._is_egress_read("deep_research"))

    def test_tool_is_registered_grouped_and_routed(self):
        names = [d["name"] for d in tools.get_declarations()]
        self.assertIn("deep_research", names)
        from core.brain import domain_routers as rm  # Phase 3 B1b INTENTIONAL: domain_routers moved to core/brain/
        membership = rm.domain_router_membership(tools)
        self.assertIn("deep_research", membership.get("route_mind", ()))

    def test_contract_names_the_tool(self):
        text = Path(__file__).parent.parent.joinpath(
            "core", "brain_capabilities.py").read_text(encoding="utf-8")
        self.assertIn("deep_research", text)


if __name__ == "__main__":
    unittest.main()
