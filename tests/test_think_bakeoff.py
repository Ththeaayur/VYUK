"""Think bake-off race harness (2026-11-05 batch).

Pins the owner-ruled design (2026-11-05, elegant design approved):
"No winner without a race, no ship without a licence." The race
harness is dev-only scaffolding: fake engines prove the MECHANICS
(same tasks, async-only, truncation marking, licence-gate
separation, no-winner-until-race); no real race ran here, no
engines arrived, no licence was cleared, no winner exists. Real
engines + real hardware + licence re-checks stay gated.
"""

import unittest

from core import think, think_bakeoff
from core.think import CONTINUATION_DIRECTIVE, ThinkBudget, ThinkRun


class FakeRaceEngine:
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


def _job(task_id="t-constraint-arith", model_key="openthinker3-7b"):
    # INTENTIONAL (bake-off 2026-11-05): min_tokens=0 so a fake
    # with short thinking completes instead of needing
    # continuation; the continuation path has its own test.
    return think_bakeoff.submit_race_job(
        "job-1", task_id, model_key,
        ThinkBudget(min_tokens=0, max_tokens=4096))


def _full_coverage_runs(nemotron_correct, openthinker_correct):
    # INTENTIONAL (bake-off 2026-11-05): ThinkRun records built
    # directly simulate a real race outcome; they are labelled
    # live=True only as a simulation of measured runs, never as
    # a race that happened.
    runs = []
    for task in think_bakeoff.RACE_TASKS:
        runs.append(ThinkRun("nemotron-nano-8b", task["id"],
                             task["id"] in nemotron_correct, 100,
                             language=task["language"], live=True))
        runs.append(ThinkRun("openthinker3-7b", task["id"],
                             task["id"] in openthinker_correct, 100,
                             language=task["language"], live=True))
    return runs


class TestRaceTasks(unittest.TestCase):
    def test_frozen_task_set(self):
        # INTENTIONAL (bake-off 2026-11-05): frozen set v1.0 —
        # changing tasks is a new version, never a silent edit.
        self.assertEqual(think_bakeoff.RACE_TASKS_VERSION, "1.0")
        self.assertEqual(len(think_bakeoff.RACE_TASKS), 8)
        self.assertEqual(list(think_bakeoff.RACE_TASK_IDS), [
            "t-plan-deps", "t-constraint-arith", "t-logic",
            "t-code-bug", "t-code-output", "t-ambiguity",
            "t-hi-arith", "t-hi-logic",
        ])
        langs = [t["language"] for t in think_bakeoff.RACE_TASKS]
        self.assertEqual(langs.count("en"), 6)
        self.assertEqual(langs.count("hi"), 2)
        for task in think_bakeoff.RACE_TASKS:
            self.assertTrue(task["prompt"].strip())
            self.assertTrue(task["expected"].strip())
            self.assertIn(task["category"],
                          ("planning", "reasoning", "code"))

    def test_grade_exact_and_normalisation(self):
        task = think_bakeoff.get_race_task("t-constraint-arith")
        self.assertTrue(think_bakeoff.grade_answer(task, "2"))
        # Punctuation / case variants pass: same answer.
        self.assertTrue(think_bakeoff.grade_answer(task, "2."))
        self.assertTrue(think_bakeoff.grade_answer(task, "  2  "))
        self.assertFalse(think_bakeoff.grade_answer(task, "3"))
        # Explanations fail by design: strict and comparative.
        self.assertFalse(
            think_bakeoff.grade_answer(task, "The answer is 2"))
        self.assertFalse(think_bakeoff.grade_answer({}, "2"))

    def test_hindi_tasks_grade(self):
        arith = think_bakeoff.get_race_task("t-hi-arith")
        logic = think_bakeoff.get_race_task("t-hi-logic")
        self.assertTrue(think_bakeoff.grade_answer(arith, "27"))
        self.assertTrue(think_bakeoff.grade_answer(logic, "मोहन"))
        self.assertFalse(think_bakeoff.grade_answer(logic, "राम"))
        self.assertIsNone(think_bakeoff.get_race_task("no-such-task"))


class TestReplacementSlot(unittest.TestCase):
    def test_slot_named(self):
        # INTENTIONAL (bake-off 2026-11-05): the winner takes ONE
        # named resident slot — quality, the resident 8B tier.
        slot = think_bakeoff.REPLACEMENT_SLOT
        self.assertEqual(slot["slot"], "quality")
        self.assertEqual(slot["resident_model"], "Qwen3-8B-Instruct-2507")
        self.assertEqual(slot["resident_gb_class"], 5.0)
        self.assertEqual(slot["budget_gb"], 8.0)
        self.assertEqual(slot["tier_rule"],
                         "INV-21 replacement-tier only")

    def test_displaced_duties_all_dispositioned(self):
        # INTENTIONAL (bake-off 2026-11-05): nothing silently
        # dropped — every duty lands in exactly one home.
        slot = think_bakeoff.REPLACEMENT_SLOT
        duties = slot["displaced_duties"]
        self.assertTrue(len(duties) >= 2)
        for duty in duties:
            self.assertIn(duty["disposition"], ("re-homed", "dropped"))
            self.assertTrue(duty["home"])
        self.assertEqual(slot["dropped"], ())

    def test_slot_consistent_with_existing_rule(self):
        result = think_bakeoff.race_slot()
        self.assertTrue(result["slot_rule_consistent"])
        competition = think.slot_competition(
            ["nemotron-nano-8b", "openthinker3-7b", "quality"])
        self.assertIsNone(competition["pick"])
        self.assertIn("quality", competition["competing"])

    def test_slot_fit_rules(self):
        # Measurement-pending, never a guessed fit.
        pending = think_bakeoff.slot_fits(None, None)
        self.assertFalse(pending["fits"])
        self.assertEqual(pending["reason"], "budget-measurement-pending")
        # 3.6 other + 4.4 candidate = 8.0 exactly: fits.
        self.assertTrue(think_bakeoff.slot_fits(4.4, 3.6)["fits"])
        # 3.6 other + 5.5 candidate = 9.1: refuses outright.
        exceeded = think_bakeoff.slot_fits(5.5, 3.6)
        self.assertFalse(exceeded["fits"])
        self.assertEqual(exceeded["reason"], "budget-exceeded")


class TestAsyncOnly(unittest.TestCase):
    def setUp(self):
        think_bakeoff.clear_race_engines()

    def tearDown(self):
        think_bakeoff.clear_race_engines()

    def test_no_sync_entries(self):
        # INTENTIONAL (bake-off 2026-11-05): async always —
        # submit/collect only, no sync entry ever.
        self.assertEqual(think_bakeoff.race_voice_path_entry_points(), [])
        for name in dir(think_bakeoff):
            if not callable(getattr(think_bakeoff, name)):
                continue
            self.assertNotIn(name, ("run", "think_sync",
                                    "generate_sync", "run_sync"))
            self.assertFalse(name.endswith("_sync"),
                             f"race harness must not gain {name}")

    def test_submit_returns_immediately_without_running(self):
        engine = FakeRaceEngine("<think>t</think>2")
        think_bakeoff.register_race_engine("openthinker3-7b", engine)
        job = _job()
        self.assertEqual(engine.calls, [])
        self.assertEqual(job.task_id, "t-constraint-arith")
        self.assertIsNone(job.result)

    def test_submit_validation(self):
        with self.assertRaises(ValueError):
            think_bakeoff.submit_race_job("j", "no-such-task",
                                          "openthinker3-7b")
        with self.assertRaises(ValueError):
            think_bakeoff.submit_race_job("j", "t-constraint-arith",
                                          "qwen3-4b-thinking")


class TestRaceRegistrySeparation(unittest.TestCase):
    def setUp(self):
        think_bakeoff.clear_race_engines()
        think.clear_engine_adapters()

    def tearDown(self):
        think_bakeoff.clear_race_engines()
        think.clear_engine_adapters()

    def test_race_registry_accepts_owed_competitor_dev_only(self):
        # INTENTIONAL (bake-off 2026-11-05): dev race allowed —
        # the OWED competitor races in dev; licence gates SHIP,
        # not the race. The production registry stays untouched.
        engine = FakeRaceEngine("<think>t</think>2")
        think_bakeoff.register_race_engine("nemotron-nano-8b", engine)
        self.assertIs(think_bakeoff.get_race_engine("nemotron-nano-8b"),
                      engine)
        self.assertIsNone(think.get_engine_adapter("nemotron-nano-8b"))
        self.assertIsNone(think.get_engine_adapter("openthinker3-7b"))
        job = think.submit_deep_think("j", "prompt", "openthinker3-7b")
        self.assertEqual(think.collect(job)["status"], "NO-DATA")

    def test_race_registry_refuses_noncompetitor_and_nonadapter(self):
        with self.assertRaises(ValueError):
            think_bakeoff.register_race_engine(
                "qwen3-4b-thinking", FakeRaceEngine("x"))
        with self.assertRaises(ValueError):
            think_bakeoff.register_race_engine("openthinker3-7b", object())

    def test_production_owed_gate_untouched(self):
        # INTENTIONAL (bake-off 2026-11-05): the ship-path gate
        # is untouched — OWED models still cannot register for
        # production, whatever the race registry allows in dev.
        with self.assertRaises(ValueError):
            think.register_engine_adapter(
                "nemotron-nano-8b", FakeRaceEngine("x"))


class TestCollect(unittest.TestCase):
    def setUp(self):
        think_bakeoff.clear_race_engines()

    def tearDown(self):
        think_bakeoff.clear_race_engines()

    def test_collect_no_adapter_is_nodata(self):
        result = think_bakeoff.collect_race_job(_job())
        self.assertEqual(result["status"], "NO-DATA")
        self.assertIsNone(result["run"])
        self.assertTrue(result["dev_only"])

    def test_collect_same_prompt_both_competitors(self):
        # INTENTIONAL (bake-off 2026-11-05): SAME real tasks —
        # both competitors receive the identical task prompt.
        nemotron = FakeRaceEngine("<think>t</think>2")
        openthinker = FakeRaceEngine("<think>t</think>2")
        think_bakeoff.register_race_engine("nemotron-nano-8b", nemotron)
        think_bakeoff.register_race_engine("openthinker3-7b", openthinker)
        task = think_bakeoff.get_race_task("t-constraint-arith")
        think_bakeoff.collect_race_job(_job(model_key="nemotron-nano-8b"))
        think_bakeoff.collect_race_job(_job(model_key="openthinker3-7b"))
        self.assertEqual(nemotron.calls[0][0], task["prompt"])
        self.assertEqual(openthinker.calls[0][0], task["prompt"])

    def test_collect_capped_is_marked(self):
        # INTENTIONAL (bake-off 2026-11-05): truncation marked,
        # never silent.
        long_thinking = " ".join(f"w{i}" for i in range(50))
        engine = FakeRaceEngine(f"<think>{long_thinking}</think>2")
        think_bakeoff.register_race_engine("openthinker3-7b", engine)
        job = think_bakeoff.submit_race_job(
            "job-1", "t-constraint-arith", "openthinker3-7b",
            ThinkBudget(min_tokens=0, max_tokens=10))
        result = think_bakeoff.collect_race_job(job)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["truncated"])
        self.assertIn("[thinking capped at budget]", result["thinking"])

    def test_collect_short_needs_continuation(self):
        # INTENTIONAL (bake-off 2026-11-05): the continuation
        # directive is prompt text, never presented as an answer.
        engine = FakeRaceEngine("<think>short</think>2")
        think_bakeoff.register_race_engine("openthinker3-7b", engine)
        job = think_bakeoff.submit_race_job(
            "job-1", "t-constraint-arith", "openthinker3-7b",
            ThinkBudget(min_tokens=256, max_tokens=4096))
        result = think_bakeoff.collect_race_job(job)
        self.assertEqual(result["status"], "needs-continuation")
        self.assertEqual(result["continuation_directive"],
                         CONTINUATION_DIRECTIVE)
        self.assertNotEqual(result["answer"],
                            CONTINUATION_DIRECTIVE)

    def test_collect_adapter_failure_and_nonstring_are_error(self):
        bad = FakeRaceEngine(exc=RuntimeError("boom"))
        think_bakeoff.register_race_engine("openthinker3-7b", bad)
        result = think_bakeoff.collect_race_job(_job())
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["run"])
        think_bakeoff.clear_race_engines()
        think_bakeoff.register_race_engine(
            "openthinker3-7b", lambda p, b: 42)
        result = think_bakeoff.collect_race_job(_job())
        self.assertEqual(result["status"], "error")

    def test_collect_fixture_is_not_live(self):
        # INTENTIONAL (bake-off 2026-11-05): fixtures never crown
        # a winner — live=True only via a registered real=True
        # adapter (the real race runner, with real engines).
        fixture = FakeRaceEngine("<think>t</think>2")
        think_bakeoff.register_race_engine("openthinker3-7b", fixture)
        self.assertFalse(
            think_bakeoff.collect_race_job(_job())["live"])
        explicit = FakeRaceEngine("<think>t</think>2")
        self.assertFalse(
            think_bakeoff.collect_race_job(_job(), adapter=explicit)["live"])
        think_bakeoff.clear_race_engines()
        think_bakeoff.register_race_engine(
            "openthinker3-7b", FakeRaceEngine("<think>t</think>2"),
            real=True)
        # Simulation only: real=True in a test stands in for the
        # real race runner; no real engine ran here.
        self.assertTrue(
            think_bakeoff.collect_race_job(_job())["live"])

    def test_collect_run_grades_and_carries_licence(self):
        engine = FakeRaceEngine("<think>t</think>2")
        think_bakeoff.register_race_engine("openthinker3-7b", engine)
        result = think_bakeoff.collect_race_job(_job())
        self.assertTrue(result["run"].correct)
        self.assertEqual(result["licence"], "Apache-2.0")
        self.assertEqual(result["licence_verdict"], "CLEAR-for-harness")
        wrong = FakeRaceEngine("<think>t</think>3")
        think_bakeoff.clear_race_engines()
        think_bakeoff.register_race_engine("openthinker3-7b", wrong)
        result = think_bakeoff.collect_race_job(_job())
        self.assertFalse(result["run"].correct)


class TestRaceVerdict(unittest.TestCase):
    def test_no_data_without_runs(self):
        verdict = think_bakeoff.race_verdict([])
        self.assertEqual(verdict["verdict"], "NO-DATA")
        self.assertIsNone(verdict["winner"])
        self.assertIsNone(verdict["ship_pick"])
        self.assertFalse(verdict["shippable"])

    def test_no_data_with_fixture_runs_only(self):
        # Fixtures never crown: live=False runs yield NO-DATA.
        runs = [ThinkRun("nemotron-nano-8b", t, True, 100, live=False)
                for t in think_bakeoff.RACE_TASK_IDS]
        runs += [ThinkRun("openthinker3-7b", t, True, 100, live=False)
                 for t in think_bakeoff.RACE_TASK_IDS]
        verdict = think_bakeoff.race_verdict(runs)
        self.assertEqual(verdict["verdict"], "NO-DATA")
        self.assertIsNone(verdict["winner"])

    def test_no_data_partial_coverage(self):
        # INTENTIONAL (bake-off 2026-11-05): partial data never
        # crowns — every competitor needs every frozen task.
        all_ids = set(think_bakeoff.RACE_TASK_IDS)
        runs = _full_coverage_runs(all_ids, all_ids)
        runs = [r for r in runs if not (
            r.model_key == "openthinker3-7b" and r.task_id == "t-logic")]
        verdict = think_bakeoff.race_verdict(runs)
        self.assertEqual(verdict["verdict"], "NO-DATA")
        self.assertIn("t-logic", verdict["missing"]["openthinker3-7b"])
        self.assertIsNone(verdict["ship_pick"])

    def test_no_data_one_engine_only(self):
        all_ids = set(think_bakeoff.RACE_TASK_IDS)
        runs = [ThinkRun("nemotron-nano-8b", t, True, 100, live=True)
                for t in think_bakeoff.RACE_TASK_IDS]
        verdict = think_bakeoff.race_verdict(runs)
        self.assertEqual(verdict["verdict"], "NO-DATA")
        self.assertEqual(set(verdict["missing"]["openthinker3-7b"]),
                         all_ids)

    def test_live_winner_and_ship_gates_pass(self):
        all_ids = set(think_bakeoff.RACE_TASK_IDS)
        half = set(list(think_bakeoff.RACE_TASK_IDS)[:4])
        runs = _full_coverage_runs(half, all_ids)
        verdict = think_bakeoff.race_verdict(
            runs, candidate_gb=4.4, other_resident_gb=3.6,
            weights_reverified=True)
        self.assertEqual(verdict["verdict"], "LIVE")
        self.assertEqual(verdict["winner"], "openthinker3-7b")
        self.assertEqual(verdict["ship_pick"], "openthinker3-7b")
        self.assertTrue(verdict["shippable"])
        self.assertEqual(verdict["ship_blockers"], [])
        self.assertEqual(verdict["hi_runs"], 4)

    def test_dirty_licence_loses_regardless_of_score(self):
        # INTENTIONAL (bake-off 2026-11-05): no ship without a
        # licence — the outscoring OWED winner still does not
        # ship, and the blocker names the licence.
        all_ids = set(think_bakeoff.RACE_TASK_IDS)
        half = set(list(think_bakeoff.RACE_TASK_IDS)[:4])
        runs = _full_coverage_runs(all_ids, half)
        verdict = think_bakeoff.race_verdict(
            runs, candidate_gb=4.4, other_resident_gb=3.6,
            weights_reverified=True)
        self.assertEqual(verdict["winner"], "nemotron-nano-8b")
        self.assertIsNone(verdict["ship_pick"])
        self.assertFalse(verdict["shippable"])
        self.assertIn("licence-OWED", verdict["ship_blockers"])

    def test_tie_no_winner(self):
        half = set(list(think_bakeoff.RACE_TASK_IDS)[:4])
        runs = _full_coverage_runs(half, half)
        verdict = think_bakeoff.race_verdict(runs)
        self.assertEqual(verdict["verdict"], "TIE-NO-WINNER")
        self.assertIsNone(verdict["winner"])
        self.assertIsNone(verdict["ship_pick"])
        self.assertIn("no-race-winner", verdict["ship_blockers"])

    def test_budget_and_weights_gates_block(self):
        all_ids = set(think_bakeoff.RACE_TASK_IDS)
        half = set(list(think_bakeoff.RACE_TASK_IDS)[:4])
        runs = _full_coverage_runs(half, all_ids)
        # Sizes unknown: measurement-pending blocks.
        verdict = think_bakeoff.race_verdict(runs)
        self.assertIn("budget-measurement-pending",
                      verdict["ship_blockers"])
        # Measured miss blocks even with a clean licence.
        verdict = think_bakeoff.race_verdict(
            runs, candidate_gb=5.5, other_resident_gb=3.6,
            weights_reverified=True)
        self.assertIn("budget-exceeded", verdict["ship_blockers"])
        self.assertIsNone(verdict["ship_pick"])
        # Fits but weights not re-verified: still blocked.
        verdict = think_bakeoff.race_verdict(
            runs, candidate_gb=4.4, other_resident_gb=3.6,
            weights_reverified=False)
        self.assertIn("weights-reverify-pending",
                      verdict["ship_blockers"])

    def test_noncompetitor_runs_ignored(self):
        all_ids = set(think_bakeoff.RACE_TASK_IDS)
        half = set(list(think_bakeoff.RACE_TASK_IDS)[:4])
        runs = _full_coverage_runs(half, all_ids)
        runs.append(ThinkRun("qwen3-4b-thinking", "t-logic", True, 10,
                             live=True))
        verdict = think_bakeoff.race_verdict(
            runs, candidate_gb=4.4, other_resident_gb=3.6,
            weights_reverified=True)
        self.assertEqual(verdict["winner"], "openthinker3-7b")
        self.assertIn("qwen3-4b-thinking", verdict["ignored_models"])


if __name__ == "__main__":
    unittest.main()
