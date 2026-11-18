"""Tests for Wave 6 Build 3 — WORK workflow learning (STAGING ONLY).

Run: python3 test_workflow_learning_w6.py  (from wave6_staging/)
No tree imports, no files written, no executor called, ever.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.workflow_learning import (
    ProvenJob,
    build_loop_plan,
    distil_workflows,
    validate_jobs,
    workflow_to_playbook_steps,
)


def proven(job_id="job-1", steps=("open settings", "toggle sounds"),
           goal="set up sounds"):
    return ProvenJob(
        job_id, goal,
        {"name": job_id, "goal_text": goal, "status": "completed",
         "reason": "", "steps": list(steps),
         "evidence": [{"source": "field-run", "detail": "ran on his machine"}]},
        field_proven=True, proof_reference="field-run-2026-10-26-001")


def unproven(**kw):
    job = proven(**kw)
    job.field_proven = False
    return job


class TestProvenDistils(unittest.TestCase):
    def test_proven_job_distils_with_evidence_ref(self):
        wf = distil_workflows([proven()])
        self.assertEqual(len(wf.steps), 2)
        step = wf.steps[0]
        self.assertEqual(step.source_job_id, "job-1")
        self.assertEqual(step.evidence_ref["proof_reference"],
                         "field-run-2026-10-26-001")
        self.assertTrue(step.evidence_ref["evidence"])
        self.assertEqual(wf.sources, ["job-1"])
        self.assertFalse(hasattr(step, "approved"))
        self.assertNotIn("approved", step.as_dict())

    def test_binder_dict_shape_accepted(self):
        # AGENT EvidenceBinder.as_dict() shape, via a plain dict.
        job = ProvenJob("job-d", "do it",
                        {"name": "sub", "goal_text": "do it",
                         "status": "completed", "reason": "",
                         "steps": ["check file"],
                         "evidence": [{"source": "funnel",
                                       "detail": "Goal complete: done"}]},
                        field_proven=True, proof_reference="run-9")
        wf = distil_workflows([job])
        self.assertEqual(wf.steps[0].text, "check file")


class TestRefusals(unittest.TestCase):
    def test_unproven_refused(self):
        with self.assertRaises(ValueError) as ctx:
            distil_workflows([unproven()])
        self.assertIn("field_proven", str(ctx.exception))

    def test_proof_reference_missing_refused(self):
        job = proven()
        job.proof_reference = ""
        with self.assertRaises(ValueError) as ctx:
            distil_workflows([job])
        self.assertIn("proof_reference", str(ctx.exception))

    def test_failed_refused(self):
        for status in ("failed", "refused", "halted", "stopped", "not-run"):
            job = proven()
            job.binder["status"] = status
            with self.assertRaises(ValueError, msg=status):
                distil_workflows([job])

    def test_unknown_status_refused(self):
        job = proven()
        job.binder["status"] = "mystery"
        with self.assertRaises(ValueError):
            distil_workflows([job])

    def test_evidence_free_refused(self):
        job = proven()
        job.binder["evidence"] = []
        with self.assertRaises(ValueError):
            distil_workflows([job])

    def test_step_free_refused(self):
        job = proven()
        job.binder["steps"] = []
        with self.assertRaises(ValueError):
            distil_workflows([job])

    def test_empty_input_refused(self):
        with self.assertRaises(ValueError):
            distil_workflows([])

    def test_mixed_refused_whole_not_partial(self):
        # The proven job alone would distil; mixed with an
        # unproven job, NOTHING may come out.
        with self.assertRaises(ValueError):
            distil_workflows([proven(), unproven(job_id="job-2")])
        ok, reasons = validate_jobs([proven(), unproven(job_id="job-2")])
        self.assertFalse(ok)
        self.assertTrue(any("job-2" in r for r in reasons))

    def test_forbidden_target_refused_whole(self):
        for target in ("assistant.db", ".env", "vault", "password"):
            job = proven(steps=(f"read {target}",))
            with self.assertRaises(ValueError, msg=target):
                distil_workflows([job])

    def test_conflicting_job_id_refused(self):
        first = proven()
        second = proven(goal="a different goal")
        with self.assertRaises(ValueError):
            distil_workflows([first, second])


class TestDedupeReported(unittest.TestCase):
    def test_duplicate_job_not_doubled_and_reported(self):
        wf = distil_workflows([proven(), proven()])
        self.assertEqual(len(wf.steps), 2)
        self.assertEqual(wf.sources, ["job-1"])
        self.assertEqual(len(wf.duplicates), 1)
        self.assertIn("duplicate", wf.duplicates[0]["reason"])

    def test_duplicate_step_within_job_not_doubled_and_reported(self):
        wf = distil_workflows(
            [proven(steps=("open settings", "open settings"))])
        self.assertEqual(len(wf.steps), 1)
        self.assertEqual(len(wf.duplicates), 1)
        self.assertEqual(wf.duplicates[0]["step"], "open settings")

    def test_same_text_different_job_is_different_step(self):
        # Provenance differs, so identity differs.
        wf = distil_workflows([proven(job_id="job-1"),
                               proven(job_id="job-2",
                                      steps=("open settings",))])
        self.assertEqual(len(wf.steps), 3)
        self.assertEqual(wf.sources, ["job-1", "job-2"])


class TestGatesNeverApproved(unittest.TestCase):
    def test_send_delete_purchase_carry_c14_not_approval(self):
        wf = distil_workflows([proven(
            steps=("send a summary message", "delete the temp file",
                   "check the result"))])
        gated = [s for s in wf.steps if s.gate_requirement]
        self.assertEqual(len(gated), 2)
        for step in gated:
            self.assertIn("C-14", step.gate_requirement)
            self.assertNotIn("approved", step.as_dict())
        plain = [s for s in wf.steps if not s.gate_requirement]
        self.assertEqual(len(plain), 1)

    def test_binder_approval_claim_stripped(self):
        job = proven(steps=[{"text": "send a summary message",
                             "approved": True}])
        wf = distil_workflows([job])
        self.assertNotIn("approved", wf.steps[0].as_dict())
        self.assertIn("C-14", wf.steps[0].gate_requirement)


class TestPlanIsProposalNotExecution(unittest.TestCase):
    def test_plan_shape_matches_agent_split_plan(self):
        # The built plan must validate through the AGENT
        # coordinator's own split_plan — same shape, no second
        # plan format.
        from core.agent_coordination import split_plan
        wf = distil_workflows([proven()])
        plan = build_loop_plan(wf, parent_goal="set up sounds")
        self.assertTrue(plan["proposed_only"])
        self.assertTrue(plan["executes_nothing"])
        self.assertNotIn("approved", str(plan))
        subs = split_plan(plan["goal_text"], plan["plan"])
        self.assertEqual([s.name for s in subs], ["step-1", "step-2"])
        self.assertEqual(subs[1].depends_on, ["step-1"])
        self.assertEqual(subs[0].context["source_job_id"], "job-1")
        self.assertEqual(
            subs[0].context["evidence_ref"]["proof_reference"],
            "field-run-2026-10-26-001")

    def test_plan_carries_gate_requirement(self):
        wf = distil_workflows(
            [proven(steps=("send a summary message",))])
        plan = build_loop_plan(wf)
        ctx = plan["plan"][0]["context"]
        self.assertEqual(ctx["requires_gate"], "C-14")
        self.assertIn("C-14", ctx["gate_requirement"])

    def test_plan_never_calls_executor(self):
        # The builder takes no executor argument at all: there
        # is nothing for it to call. Passing one must fail on
        # the signature, not silently ignore it.
        wf = distil_workflows([proven()])
        with self.assertRaises(TypeError):
            build_loop_plan(wf, executor=lambda: (_ for _ in ()).throw(
                AssertionError("executor must never be called")))

    def test_empty_workflow_plan_refused(self):
        with self.assertRaises(ValueError):
            build_loop_plan(None)

    def test_module_executes_nothing(self):
        # Source-level guard: no funnel, dispatch, subprocess,
        # or file-write call in the learner itself.
        # Integration adaptation: in staging this test sat beside
        # the module; in the tree it sits in tests/ and the module
        # in core/, so the source guard reads core/ (same module,
        # same guard, only the path changed).
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "core", "workflow_learning.py")
        src = open(path).read()
        for banned in ("subprocess", "os.system", "open(", "run_goal("):
            self.assertNotIn(banned, src)
        self.assertNotIn("import playbook_store", src)


class TestPlaybookConversionIsNotAStore(unittest.TestCase):
    def test_conversion_returns_steps_writes_nothing(self):
        before = set(os.listdir(os.path.dirname(os.path.abspath(__file__))))
        wf = distil_workflows([proven()])
        rows = workflow_to_playbook_steps(wf)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["proof_reference"],
                         "field-run-2026-10-26-001")
        self.assertEqual(rows[0]["text"], "open settings")
        after = set(os.listdir(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
