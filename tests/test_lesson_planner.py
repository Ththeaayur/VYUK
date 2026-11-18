"""U2 — lesson planner: a topic plans to dependency-ordered bites
(bite + check + gold + rubric), ordered lowest-mastery-first from
the U1 student model, through the B23 approve-plan-once rules.

Lab leg only. The real-Windows field leg — a real lesson plan for
MA 105 on his machine, approved and run, with bites ordered by his
actual mastery — is OWED, not this batch.

Gap proved first (audit T1): B22 decomposes GOALS into tool steps
(``build_decompose_prompt`` — "Break this goal into sub-goals, and
each sub-goal into a small list of tool steps";
``run_decomposed_goal``, task_coordinator_execution.py:964) — never
a TOPIC into teachable bites; the flashcard deck is an
alphabetically sorted pile (``_cards()``,
skills_study_helpers.py:29) and ``quiz_me`` picks one card at
random (skills_study_drills.py:33-40). No topic -> bites path
exists anywhere in the tree (no "lesson_plan" / teaching "bite"
in core/ outside the U1 docstring's forward reference).

What this suite proves: the bite schema (every bite carries bite +
check + gold + rubric; a bite missing any field is refused, never
planned); dependency order (a bite never precedes its
prerequisite, even when the brain lists them out of order);
mastery-driven re-ordering (lowest-mastery-first in the ready set;
a mastered bite is skipped, never re-taught); determinism (same
topic + same mastery -> the identical plan, run twice); cold start
(no history -> the first bite is a diagnostic); approval (an
unapproved plan never runs; an approved plan runs exactly its
approved bites; a timeout / ask-each-time / no handler / error
approves nothing); cycles and unknown prerequisites fail honestly;
no brain fails honestly (never a guessed plan); the planner reads
the student model and never writes it.
"""

import json
import unittest

from core import plan_approval as pa
from core.brain import lesson_planner as lp
from core.brain import student_model as sm

SKILL_A = "ma105:surfaces:parametrisation"
SKILL_B = "ma105:surfaces:tangent-plane"
SKILL_C = "ma105:surfaces:area-element"
SKILL_X = "ma105:double-integrals:limits-from-region"
SKILL_Y = "ma105:double-integrals:jacobian"
SKILL_M = "ma105:green:statement"


def _bite(skill, requires=(), reinforce=None, **overrides):
    raw = {
        "skill": skill,
        "bite": f"Teach {skill}.",
        "check": f"Check {skill}?",
        "gold": f"The answer for {skill}.",
        "rubric": f"Correct when the {skill} idea is used.",
        "requires": list(requires),
    }
    if reinforce is not None:
        raw["reinforce"] = reinforce
    raw.update(overrides)
    return raw


def _brain(bites):
    """A fake plan brain returning this bite list as JSON."""
    reply = json.dumps({"bites": bites})

    def ask(prompt):
        return reply

    return ask


def _plan(topic="Surfaces", bites=(), model=None, brain_name="fake"):
    return lp.plan_topic(topic, student_model=model,
                         ask_brain=_brain(list(bites)),
                         brain_name=brain_name)


class TestBiteSchema(unittest.TestCase):
    def test_full_bite_parses(self):
        parsed, error = lp.parse_topic_plan_text(
            json.dumps({"bites": [_bite(SKILL_A)]}))
        self.assertEqual(error, "")
        self.assertEqual(len(parsed["bites"]), 1)
        bite = parsed["bites"][0]
        self.assertEqual(bite.skill, SKILL_A)
        self.assertEqual(bite.bite, f"Teach {SKILL_A}.")
        self.assertEqual(bite.check, f"Check {SKILL_A}?")
        self.assertEqual(bite.gold, f"The answer for {SKILL_A}.")
        self.assertTrue(bite.rubric)
        # No reinforce given: the edge falls back to the bite
        # itself (re-teach the bite).
        self.assertEqual(bite.reinforce, SKILL_A)

    def test_missing_field_is_refused(self):
        for field in ("bite", "check", "gold", "rubric", "skill"):
            raw = _bite(SKILL_A)
            del raw[field]
            parsed, error = lp.parse_topic_plan_text(
                json.dumps({"bites": [raw]}))
            self.assertIsNone(parsed, field)
            self.assertIn(field, error, field)

    def test_requires_must_be_a_list(self):
        raw = _bite(SKILL_A)
        raw["requires"] = SKILL_B  # a bare string, not a list
        parsed, error = lp.parse_topic_plan_text(
            json.dumps({"bites": [raw]}))
        self.assertIsNone(parsed)
        self.assertIn("requires", error)

    def test_duplicate_skill_is_refused(self):
        parsed, error = lp.parse_topic_plan_text(
            json.dumps({"bites": [_bite(SKILL_A), _bite(SKILL_A)]}))
        self.assertIsNone(parsed)
        self.assertIn("twice", error)

    def test_reinforce_edge_is_kept(self):
        parsed, error = lp.parse_topic_plan_text(
            json.dumps({"bites": [
                _bite(SKILL_A),
                _bite(SKILL_B, requires=(SKILL_A,),
                      reinforce=SKILL_A)]}))
        self.assertEqual(error, "")
        self.assertEqual(parsed["bites"][1].reinforce, SKILL_A)


class TestOrdering(unittest.TestCase):
    def test_dependency_order_beats_brain_order(self):
        # The brain lists them C, A, B — the plan must teach
        # A -> B -> C (a bite never precedes its prerequisite).
        plan, error = _plan(bites=[
            _bite(SKILL_C, requires=(SKILL_B,)),
            _bite(SKILL_A),
            _bite(SKILL_B, requires=(SKILL_A,)),
        ])
        self.assertEqual(error, "")
        self.assertEqual([b.skill for b in plan.bites],
                         [SKILL_A, SKILL_B, SKILL_C])

    def test_lowest_mastery_first_in_the_ready_set(self):
        model = sm.StudentModel()
        # X answered wrong once (p drops below the 0.30 prior);
        # Y answered right once (p rises above it).
        model.record_graded(SKILL_X, False)
        model.record_graded(SKILL_Y, True)
        self.assertLess(model.get_skill(SKILL_X)["p"],
                        model.get_skill(SKILL_Y)["p"])
        # The brain lists Y first; the plan must still teach
        # the weaker skill first.
        plan, error = _plan(bites=[_bite(SKILL_Y), _bite(SKILL_X)],
                            model=model)
        self.assertEqual(error, "")
        self.assertEqual([b.skill for b in plan.bites],
                         [SKILL_X, SKILL_Y])

    def test_mastered_bite_is_skipped_not_retaught(self):
        model = sm.StudentModel()
        model.record_graded(SKILL_M, True, novel=True)
        model.record_graded(SKILL_M, True, novel=True)
        self.assertTrue(model.is_mastered(SKILL_M))
        plan, error = _plan(bites=[_bite(SKILL_M), _bite(SKILL_A)],
                            model=model)
        self.assertEqual(error, "")
        self.assertEqual([b.skill for b in plan.bites], [SKILL_A])
        self.assertEqual(plan.skipped_mastered, [SKILL_M])

    def test_prerequisite_already_mastered_is_satisfied(self):
        model = sm.StudentModel()
        model.record_graded(SKILL_M, True, novel=True)
        model.record_graded(SKILL_M, True, novel=True)
        # B requires M, but M is mastered and not a bite here —
        # the plan still succeeds; B can go first.
        plan, error = _plan(bites=[_bite(SKILL_B,
                                        requires=(SKILL_M,))],
                            model=model)
        self.assertEqual(error, "")
        self.assertEqual([b.skill for b in plan.bites], [SKILL_B])

    def test_determinism_same_topic_same_mastery(self):
        model = sm.StudentModel()
        model.record_graded(SKILL_X, False)
        bites = [_bite(SKILL_Y), _bite(SKILL_X), _bite(SKILL_A)]
        first, error1 = _plan(bites=bites, model=model)
        second, error2 = _plan(bites=bites, model=model)
        self.assertEqual(error1, "")
        self.assertEqual(error2, "")
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.plan_id, second.plan_id)


class TestColdStart(unittest.TestCase):
    def test_no_history_first_bite_is_diagnostic(self):
        plan, error = _plan(bites=[_bite(SKILL_A), _bite(SKILL_B)])
        self.assertEqual(error, "")
        self.assertTrue(plan.diagnostic)
        self.assertTrue(plan.bites[0].diagnostic)
        self.assertFalse(plan.bites[1].diagnostic)

    def test_with_history_no_diagnostic(self):
        model = sm.StudentModel()
        model.record_graded(SKILL_A, True)
        plan, error = _plan(bites=[_bite(SKILL_A), _bite(SKILL_B)],
                            model=model)
        self.assertEqual(error, "")
        self.assertFalse(plan.diagnostic)
        self.assertFalse(any(b.diagnostic for b in plan.bites))


class TestHonestFailures(unittest.TestCase):
    def test_dependency_cycle_fails(self):
        plan, error = _plan(bites=[
            _bite(SKILL_A, requires=(SKILL_B,)),
            _bite(SKILL_B, requires=(SKILL_A,)),
        ])
        self.assertIsNone(plan)
        self.assertIn("cycle", error)

    def test_self_dependency_fails(self):
        plan, error = _plan(bites=[
            _bite(SKILL_A, requires=(SKILL_A,)),
        ])
        self.assertIsNone(plan)
        self.assertIn("cycle", error)

    def test_unknown_prerequisite_fails(self):
        plan, error = _plan(bites=[
            _bite(SKILL_B, requires=("ma105:unknown:skill",)),
        ])
        self.assertIsNone(plan)
        self.assertIn("ma105:unknown:skill", error)

    def test_unknown_reinforce_fails(self):
        plan, error = _plan(bites=[
            _bite(SKILL_B, reinforce="ma105:unknown:skill"),
        ])
        self.assertIsNone(plan)
        self.assertIn("ma105:unknown:skill", error)

    def test_no_brain_returns_nothing(self):
        plan, error = lp.plan_topic(
            "Surfaces", student_model=sm.StudentModel(),
            ask_brain=lambda prompt: "")
        self.assertIsNone(plan)
        self.assertIn("no plan text", error)

    def test_erroring_brain_fails_honestly(self):
        def ask(prompt):
            raise RuntimeError("brain down")

        plan, error = lp.plan_topic(
            "Surfaces", student_model=sm.StudentModel(),
            ask_brain=ask)
        self.assertIsNone(plan)
        self.assertIn("unavailable", error)

    def test_no_brain_callable_fails(self):
        plan, error = lp.plan_topic(
            "Surfaces", student_model=sm.StudentModel(),
            ask_brain=None)
        self.assertIsNone(plan)
        self.assertIn("no brain", error)

    def test_empty_topic_fails(self):
        plan, error = lp.plan_topic(
            "  ", student_model=sm.StudentModel(),
            ask_brain=_brain([_bite(SKILL_A)]))
        self.assertIsNone(plan)
        self.assertIn("no topic", error)

    def test_malformed_replies_fail(self):
        cases = [
            ("no json at all", "no JSON object"),
            ("{not json}", "not valid JSON"),
            (json.dumps({"subgoals": []}), "no 'bites' list"),
            (json.dumps({"bites": "nope"}), "no 'bites' list"),
            (json.dumps({"bites": ["nope"]}), "not an object"),
        ]
        for reply, fragment in cases:
            parsed, error = lp.parse_topic_plan_text(reply)
            self.assertIsNone(parsed, reply)
            self.assertIn(fragment, error, reply)

    def test_over_cap_plan_fails(self):
        bites = [_bite(f"ma105:topic:skill-{i:02d}")
                 for i in range(lp.MAX_BITES_PER_PLAN + 1)]
        parsed, error = lp.parse_topic_plan_text(
            json.dumps({"bites": bites}))
        self.assertIsNone(parsed)
        self.assertIn(str(lp.MAX_BITES_PER_PLAN), error)

    def test_planner_never_writes_mastery(self):
        model = sm.StudentModel()
        model.record_graded(SKILL_X, False)
        before = model.mastery_summary()
        plan, error = _plan(bites=[_bite(SKILL_X), _bite(SKILL_Y)],
                            model=model)
        self.assertEqual(error, "")
        self.assertEqual(model.mastery_summary(), before)


class TestApproval(unittest.TestCase):
    def _approved_plan(self):
        plan, error = _plan(bites=[_bite(SKILL_A), _bite(SKILL_B)])
        self.assertEqual(error, "")
        return plan

    def test_unapproved_plan_never_runs(self):
        plan = self._approved_plan()
        bites, error = plan.start()
        self.assertEqual(bites, [])
        self.assertIn("never approved", error)

    def test_approved_plan_runs_its_approved_bites(self):
        plan = self._approved_plan()
        result = plan.approve([0, 1])
        self.assertEqual(result["approved"], [0, 1])
        self.assertEqual(result["refused"], [])
        bites, error = plan.start()
        self.assertEqual(error, "")
        self.assertEqual([b.skill for b in bites],
                         [SKILL_A, SKILL_B])

    def test_partial_approval_runs_only_approved_bites(self):
        plan = self._approved_plan()
        plan.approve([1])
        bites, error = plan.start()
        self.assertEqual(error, "")
        self.assertEqual([b.skill for b in bites], [SKILL_B])

    def test_out_of_range_index_is_refused(self):
        plan = self._approved_plan()
        result = plan.approve([0, 7, -1, "1"])
        self.assertEqual(result["approved"], [0])
        self.assertEqual(len(result["refused"]), 3)
        bites, error = plan.start()
        self.assertEqual([b.skill for b in bites], [SKILL_A])

    def test_timeout_approves_nothing(self):
        plan = self._approved_plan()
        result = plan.approve([0, 1], outcome="timeout")
        self.assertEqual(result["approved"], [])
        bites, error = plan.start()
        self.assertEqual(bites, [])
        self.assertIn("timed out", error)

    def test_ask_each_time_and_no_handler_approve_nothing(self):
        for outcome in ("ask_each_time", "no_handler", "error"):
            plan = self._approved_plan()
            plan.approve([0, 1], outcome=outcome)
            bites, error = plan.start()
            self.assertEqual(bites, [], outcome)
            self.assertTrue(error, outcome)

    def test_approved_with_no_indices_approves_nothing(self):
        plan = self._approved_plan()
        result = plan.approve([], outcome="approved")
        self.assertEqual(result["approved"], [])
        self.assertEqual(plan.approval_outcome, "ask_each_time")
        bites, error = plan.start()
        self.assertEqual(bites, [])

    def test_second_approval_replaces_the_first(self):
        plan = self._approved_plan()
        plan.approve([0, 1])
        plan.approve([1])
        bites, error = plan.start()
        self.assertEqual([b.skill for b in bites], [SKILL_B])

    def test_unknown_outcome_approves_nothing(self):
        plan = self._approved_plan()
        result = plan.approve([0], outcome="maybe")
        self.assertEqual(result["approved"], [])
        self.assertEqual(len(result["refused"]), 1)
        bites, error = plan.start()
        self.assertEqual(bites, [])

    def test_dialog_path_through_the_real_b23_machinery(self):
        plan = self._approved_plan()

        def fake_dialog(task, preview):
            # The dialog shows the plan once; the human
            # approves the first bite only.
            self.assertEqual(task.id, plan.plan_id)
            self.assertEqual(len(task.steps), 2)
            return [0]

        pa.register_plan_handler(fake_dialog)
        try:
            indices, outcome = pa.request_plan_decision_ex(
                plan.as_dialog_task())
        finally:
            pa.register_plan_handler(None)
        self.assertEqual(outcome, "approved")
        plan.approve(indices, outcome=outcome)
        bites, error = plan.start()
        self.assertEqual(error, "")
        self.assertEqual([b.skill for b in bites], [SKILL_A])

    def test_no_handler_means_no_approval(self):
        plan = self._approved_plan()
        pa.register_plan_handler(None)
        indices, outcome = pa.request_plan_decision_ex(
            plan.as_dialog_task())
        self.assertEqual(indices, [])
        self.assertEqual(outcome, "no_handler")
        plan.approve(indices, outcome=outcome)
        bites, error = plan.start()
        self.assertEqual(bites, [])
        self.assertIn("no approval dialog", error)

    def test_empty_plan_runs_zero_bites_without_approval(self):
        model = sm.StudentModel()
        model.record_graded(SKILL_A, True, novel=True)
        model.record_graded(SKILL_A, True, novel=True)
        plan, error = _plan(bites=[_bite(SKILL_A)], model=model)
        self.assertEqual(error, "")
        self.assertEqual(plan.bites, [])
        # Nothing left to teach — zero bites, not an error,
        # and nothing to approve.
        bites, error = plan.start()
        self.assertEqual(bites, [])
        self.assertEqual(error, "")


class TestSummary(unittest.TestCase):
    def test_summary_is_plain_words(self):
        model = sm.StudentModel()
        model.record_graded(SKILL_M, True, novel=True)
        model.record_graded(SKILL_M, True, novel=True)
        plan, error = _plan(bites=[_bite(SKILL_M), _bite(SKILL_A)],
                            model=model)
        self.assertEqual(error, "")
        text = plan.summary()
        self.assertIn("Surfaces", text)
        self.assertIn("1 bite(s)", text)
        self.assertIn("1 skipped as already mastered", text)
        self.assertIn("diagnostic", text)
        self.assertIn("Not yet approved", text)


if __name__ == "__main__":
    unittest.main()
