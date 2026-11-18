"""Think engine adapter seam (2026-11-03 batch).

Pins the coordinator seam the harness itself documents
(``wiring_seam``): an engine adapter is ``generate(prompt, budget)
-> str``; ``collect`` runs a registered (or explicitly passed)
adapter through ``split_thinking`` + ``enforce_budget`` and stays
honest on every path (NO-DATA with no adapter, needs-continuation
on short thinking, capped marked, error on adapter failure).
Registration is licence-gated: only CLEAR-for-harness models may
register. No live weights exist on this VM, so every engine here
is an injected fake — the tests prove the SEAM, and the live
weights + runtime + field proof stay gated in code (see the
LIVE-ENGINE GATE comment in core/think.py).
"""
import unittest

from core import think
from core.think import (
    CONTINUATION_DIRECTIVE,
    TRUNCATION_MARKER,
    DeepThinkJob,
    ThinkBudget,
    ThinkRun,
)


class FakeEngine:
    """An adapter object holding a .generate attribute."""

    def __init__(self, output=None, exc=None):
        self.output = output
        self.exc = exc
        self.calls = []

    def generate(self, prompt, budget):
        self.calls.append((prompt, budget))
        if self.exc is not None:
            raise self.exc
        return self.output


def _job(model_key="qwen3-4b-thinking", budget=None):
    return think.submit_deep_think(
        "job-1", "why is the sky blue?", model_key,
        budget if budget is not None else ThinkBudget(
            min_tokens=0, max_tokens=4096))


class TestRegistryLicenceGate(unittest.TestCase):
    def setUp(self):
        think.clear_engine_adapters()

    def tearDown(self):
        think.clear_engine_adapters()

    def test_register_unknown_key_refused(self):
        with self.assertRaises(ValueError):
            think.register_engine_adapter("no-such-model", lambda p, b: "")

    def test_register_owed_model_refused(self):
        # Nemotron stays OWED: the gate must refuse it even by accident.
        with self.assertRaises(ValueError):
            think.register_engine_adapter(
                "nemotron-nano-8b", lambda p, b: "")

    def test_register_non_adapter_refused(self):
        with self.assertRaises(ValueError):
            think.register_engine_adapter("qwen3-4b-thinking", 42)

    def test_register_get_overwrite_clear(self):
        first = FakeEngine("first")
        second = FakeEngine("second")
        think.register_engine_adapter("qwen3-4b-thinking", first)
        self.assertIs(think.get_engine_adapter("qwen3-4b-thinking"), first)
        think.register_engine_adapter("qwen3-4b-thinking", second)
        self.assertIs(think.get_engine_adapter("qwen3-4b-thinking"), second)
        think.clear_engine_adapters()
        self.assertIsNone(think.get_engine_adapter("qwen3-4b-thinking"))

    def test_register_plain_function_adapter(self):
        # The seam accepts a bare callable as well as a .generate holder.
        think.register_engine_adapter(
            "r1-distill-qwen-7b", lambda p, b: "answer only")
        self.assertTrue(callable(
            think.get_engine_adapter("r1-distill-qwen-7b")))


class TestCollectPaths(unittest.TestCase):
    def setUp(self):
        think.clear_engine_adapters()

    def tearDown(self):
        think.clear_engine_adapters()

    def test_collect_no_adapter_is_nodata(self):
        result = think.collect(_job())
        self.assertEqual(result["status"], "NO-DATA")
        self.assertIsNone(result["thinking"])
        self.assertIsNone(result["answer"])
        self.assertIn("No deep-think engine", result["reason"])

    def test_collect_registered_adapter_completed(self):
        engine = FakeEngine("<think>step one step two</think>the answer")
        think.register_engine_adapter("qwen3-4b-thinking", engine)
        job = _job()
        result = think.collect(job)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["thinking"], "step one step two")
        self.assertEqual(result["answer"], "the answer")
        self.assertEqual(result["action"], "kept")
        # The adapter saw exactly the job's prompt and budget.
        self.assertEqual(engine.calls, [(job.prompt, job.budget)])

    def test_collect_explicit_adapter_without_registration(self):
        job = _job()
        result = think.collect(
            job, adapter=lambda p, b: "plain answer, no marker")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["thinking"], "")
        self.assertEqual(result["answer"], "plain answer, no marker")

    def test_collect_capped_is_marked(self):
        engine = FakeEngine(
            "<think>one two three four five</think>done")
        think.register_engine_adapter("qwen3-4b-thinking", engine)
        job = _job(budget=ThinkBudget(min_tokens=0, max_tokens=2))
        result = think.collect(job)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["action"], "capped")
        self.assertIn(TRUNCATION_MARKER, result["thinking"])
        self.assertEqual(result["tokens_after"], 2)

    def test_collect_short_thinking_needs_continuation(self):
        engine = FakeEngine("<think>too short</think>early answer")
        think.register_engine_adapter("qwen3-4b-thinking", engine)
        job = think.submit_deep_think(
            "job-2", "hard problem", "qwen3-4b-thinking")
        result = think.collect(job)
        self.assertEqual(result["status"], "needs-continuation")
        self.assertEqual(result["action"], "extend")
        self.assertEqual(result["continuation_directive"],
                         CONTINUATION_DIRECTIVE)
        # The partial answer is present but the status says incomplete.
        self.assertEqual(result["answer"], "early answer")

    def test_collect_adapter_failure_is_error_not_faked(self):
        engine = FakeEngine(exc=RuntimeError("weights missing"))
        think.register_engine_adapter("qwen3-4b-thinking", engine)
        result = think.collect(_job())
        self.assertEqual(result["status"], "error")
        self.assertIn("engine adapter failed", result["reason"])
        self.assertIsNone(result["thinking"])
        self.assertIsNone(result["answer"])

    def test_collect_nonstring_output_is_error(self):
        engine = FakeEngine(output={"not": "a string"})
        think.register_engine_adapter("qwen3-4b-thinking", engine)
        result = think.collect(_job())
        self.assertEqual(result["status"], "error")
        self.assertIn("must return str", result["reason"])

    def test_collect_explicit_adapter_for_owed_model_is_gated(self):
        # The collect-side gate closes the explicit-pass bypass:
        # a Nemotron adapter cannot run even when passed directly.
        job = think.submit_deep_think(
            "job-3", "hard problem", "nemotron-nano-8b")
        result = think.collect(job, adapter=lambda p, b: "answer")
        self.assertEqual(result["status"], "error")
        self.assertIn("licence verdict", result["reason"])


class TestHarnessUnchanged(unittest.TestCase):
    def setUp(self):
        think.clear_engine_adapters()

    def tearDown(self):
        think.clear_engine_adapters()

    def test_no_synchronous_voice_entry_points(self):
        # Mirrors the test_phase3 pin: silent thinking stays silent.
        self.assertEqual(think.voice_path_entry_points(), [])
        for name in ("think_sync", "generate_sync", "run"):
            self.assertFalse(hasattr(think, name),
                             f"deep-think harness must not gain {name}")

    def test_submit_validation_unchanged(self):
        with self.assertRaises(ValueError):
            think.submit_deep_think("j", "prompt", "no-such-model")
        with self.assertRaises(ValueError):
            think.submit_deep_think("j", "   ", "qwen3-4b-thinking")

    def test_bakeoff_still_nodata_without_live_runs(self):
        self.assertEqual(think.bakeoff_verdict([])["verdict"], "NO-DATA")
        fixture = ThinkRun("qwen3-4b-thinking", "t1", True, 100, live=False)
        self.assertEqual(think.bakeoff_verdict([fixture])["verdict"],
                         "NO-DATA")

    def test_bakeoff_live_runs_still_pick_a_winner(self):
        runs = [
            ThinkRun("qwen3-4b-thinking", "t1", True, 100, live=True),
            ThinkRun("qwen3-4b-thinking", "t2", True, 120, live=True),
            ThinkRun("r1-distill-qwen-7b", "t1", False, 90, live=True),
            ThinkRun("r1-distill-qwen-7b", "t2", True, 90, live=True),
        ]
        verdict = think.bakeoff_verdict(runs)
        self.assertEqual(verdict["verdict"], "LIVE")
        self.assertEqual(verdict["winner"], "qwen3-4b-thinking")


class TestLicenceVerdicts(unittest.TestCase):
    def test_cleared_models(self):
        for key in ("qwen3-4b-thinking", "r1-distill-qwen-7b",
                    "openthinker3-7b"):
            verdict = think.licence_verdict(key)
            self.assertEqual(verdict["verdict"], "CLEAR-for-harness", key)

    def test_nemotron_still_owed(self):
        verdict = think.licence_verdict("nemotron-nano-8b")
        self.assertEqual(verdict["verdict"], "OWED")
        self.assertEqual(verdict["licence"], "OWED")

    def test_corrected_publisher_repo_ids(self):
        self.assertEqual(
            think.MODELS["nemotron-nano-8b"]["repo"],
            "nvidia/Llama-3.1-Nemotron-Nano-8B-v1")
        self.assertEqual(
            think.MODELS["openthinker3-7b"]["repo"],
            "open-thoughts/OpenThinker3-7B")


if __name__ == "__main__":
    unittest.main()
