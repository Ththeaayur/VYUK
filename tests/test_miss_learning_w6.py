"""Tests for Wave 6 Build 4 — MISS extensions + Track K + J9/M1 (STAGING ONLY).

Run: python3 test_miss_learning_w6.py  (from wave6_staging/)
No tree imports, no files written, no executor called, ever.
One test distils a real WORK workflow and compounds it, proving
the MISS layer reuses the WORK shape instead of inventing its own.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.miss_extensions_learning import (
    CompoundedWorkflow,
    assess_capability_claims,
    capability_map_from_workflows,
    compound_to_playbook_steps,
    compound_workflows,
    validate_workflows,
)
from core.workflow_learning import ProvenJob, distil_workflows


def evidence(proof="field-run-2026-10-26-001"):
    return {"job_id": "job-1", "binder_name": "job-1",
            "proof_reference": proof,
            "evidence": [{"source": "field-run", "detail": "ran on his machine"}]}


def step(text, job="job-1", proof="field-run-2026-10-26-001", **extra):
    data = {"id": f"{job}-{abs(hash(text)) % 10000}", "text": text,
            "source_job_id": job, "evidence_ref": evidence(proof)}
    data.update(extra)
    return data


def workflow(name="sounds-setup", steps=None, job="job-1"):
    return {"name": name,
            "steps": steps if steps is not None
            else [step("open settings"), step("toggle sounds")],
            "sources": [job],
            "proposed_only": True, "executes_nothing": True}


def unproven_workflow():
    bad = step("open settings")
    bad["evidence_ref"] = {"job_id": "job-9", "binder_name": "job-9",
                           "proof_reference": "",
                           "evidence": [{"source": "f", "detail": "d"}]}
    return workflow("unproven-one", steps=[bad], job="job-9")


class TestCompounding(unittest.TestCase):
    def test_proven_workflows_compound_with_evidence_preserved(self):
        wf1 = workflow("sounds-setup")
        wf2 = workflow("notes-setup",
                       steps=[step("open notes", job="job-2",
                                   proof="field-run-2026-10-26-002")],
                       job="job-2")
        compound = compound_workflows([wf1, wf2], "morning-setup")
        self.assertEqual(
            [s["text"] for s in compound.steps],
            ["open settings", "toggle sounds", "open notes"])
        self.assertEqual(compound.sources, ["job-1", "job-2"])
        self.assertEqual(compound.workflow_names,
                         ["sounds-setup", "notes-setup"])
        self.assertEqual(
            compound.steps[0]["evidence_ref"]["proof_reference"],
            "field-run-2026-10-26-001")
        self.assertTrue(compound.as_dict()["proposed_only"])
        self.assertTrue(compound.as_dict()["executes_nothing"])

    def test_real_work_workflow_compounds(self):
        job = ProvenJob(
            "job-1", "set up sounds",
            {"name": "job-1", "goal_text": "set up sounds",
             "status": "completed", "reason": "",
             "steps": ["open settings", "toggle sounds"],
             "evidence": [{"source": "field-run",
                           "detail": "ran on his machine"}]},
            field_proven=True, proof_reference="field-run-2026-10-26-001")
        wf = distil_workflows([job], "sounds-setup")
        compound = compound_workflows([wf.as_dict()], "compound-1")
        self.assertEqual([s["text"] for s in compound.steps],
                         ["open settings", "toggle sounds"])

    def test_unproven_workflow_refused_whole(self):
        with self.assertRaises(ValueError):
            compound_workflows([unproven_workflow()])
        ok, reasons = validate_workflows([unproven_workflow()])
        self.assertFalse(ok)
        self.assertTrue(any("proof_reference" in r for r in reasons))

    def test_mixed_proven_and_unproven_refused_whole(self):
        with self.assertRaises(ValueError):
            compound_workflows([workflow(), unproven_workflow()])

    def test_bare_step_list_refused(self):
        with self.assertRaises(ValueError):
            compound_workflows([["open settings", "toggle sounds"]])

    def test_empty_input_refused(self):
        with self.assertRaises(ValueError):
            compound_workflows([])

    def test_step_without_provenance_refused(self):
        bad = step("open settings")
        del bad["source_job_id"]
        with self.assertRaises(ValueError):
            compound_workflows([workflow(steps=[bad])])

    def test_forbidden_target_refused_whole(self):
        bad = workflow(steps=[step("read assistant.db for settings")])
        with self.assertRaises(ValueError):
            compound_workflows([bad])
        for target in ("vault", ".env", "credential"):
            with self.subTest(target=target):
                bad = workflow(steps=[step(f"open {target} now")])
                with self.assertRaises(ValueError):
                    compound_workflows([bad])

    def test_duplicate_same_job_step_folded_and_reported(self):
        wf = workflow(steps=[step("open settings"), step("open settings")])
        compound = compound_workflows([wf, wf])
        self.assertEqual([s["text"] for s in compound.steps],
                         ["open settings"])
        self.assertTrue(compound.duplicates)

    def test_same_text_different_job_is_different_step(self):
        wf1 = workflow("a", steps=[step("open settings", job="job-1")])
        wf2 = workflow("b", steps=[step("open settings", job="job-2",
                                        proof="field-run-2026-10-26-002")],
                       job="job-2")
        compound = compound_workflows([wf1, wf2])
        self.assertEqual(len(compound.steps), 2)

    def test_approval_claim_stripped_never_honoured(self):
        wf = workflow(steps=[step("send a note", approved=True,
                                 gate_requirement="C-14 approve-first "
                                                  "required before send")])
        compound = compound_workflows([wf])
        self.assertNotIn("approved", compound.steps[0])
        self.assertEqual(compound.steps[0]["gate_requirement"],
                         "C-14 approve-first required before send")

    def test_no_step_invented(self):
        wf = workflow(steps=[step("open settings")])
        compound = compound_workflows([wf])
        self.assertEqual([s["text"] for s in compound.steps],
                         ["open settings"])


class TestCapabilityMap(unittest.TestCase):
    def test_proven_workflow_becomes_capability(self):
        result = capability_map_from_workflows([workflow()])
        self.assertEqual(len(result["capabilities"]), 1)
        self.assertEqual(result["capabilities"][0]["capability"],
                         "sounds-setup")
        self.assertEqual(result["capabilities"][0]["sources"], ["job-1"])
        self.assertEqual(result["unproven"], [])

    def test_unproven_workflow_listed_unproven_never_capability(self):
        result = capability_map_from_workflows(
            [workflow(), unproven_workflow()])
        self.assertEqual(len(result["capabilities"]), 1)
        self.assertEqual(len(result["unproven"]), 1)

    def test_claim_without_workflow_is_unproven_not_capability(self):
        result = assess_capability_claims(
            [{"capability": "fly the laptop", "workflows": []},
             {"capability": "sounds-setup", "workflows": [workflow()]}])
        self.assertEqual([c["capability"] for c in result["capabilities"]],
                         ["sounds-setup"])
        self.assertEqual([u["capability"] for u in result["unproven"]],
                         ["fly the laptop"])

    def test_claim_with_unproven_workflow_is_unproven(self):
        result = assess_capability_claims(
            [{"capability": "x", "workflows": [unproven_workflow()]}])
        self.assertEqual(result["capabilities"], [])
        self.assertEqual(len(result["unproven"]), 1)

    def test_track_k_candidate_without_proven_job_stays_unproven(self):
        # The Track K remainder rule, exercised with a named
        # candidate that has no proven job behind it.
        result = assess_capability_claims(
            [{"capability": "track-k-remainder-item", "workflows": None}])
        self.assertEqual(result["capabilities"], [])
        self.assertEqual(result["unproven"][0]["capability"],
                         "track-k-remainder-item")


class TestPlaybookConversion(unittest.TestCase):
    def test_conversion_carries_proof_and_gate_writes_nothing(self):
        wf = workflow(steps=[
            step("open settings"),
            step("send a note",
                 gate_requirement="C-14 approve-first required before send")])
        compound = compound_workflows([wf])
        with tempfile.TemporaryDirectory() as tmp:
            before = set(os.listdir(tmp))
            steps = compound_to_playbook_steps(compound)
            self.assertEqual(set(os.listdir(tmp)), before)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["proof_reference"],
                         "field-run-2026-10-26-001")
        self.assertEqual(steps[1]["gate_requirement"],
                         "C-14 approve-first required before send")
        self.assertNotIn("approved", steps[1])
        # No store attribute, no file path: this is a conversion.
        self.assertFalse(hasattr(compound_to_playbook_steps, "store"))
        self.assertFalse(hasattr(compound_to_playbook_steps, "path"))

    def test_conversion_refuses_deletion_step(self):
        bad = CompoundedWorkflow(
            "bad", [step("delete old note",
                         gate_requirement="C-14 approve-first "
                                          "required before delete")],
            ["job-1"], ["bad"], [])
        with self.assertRaises(ValueError):
            compound_to_playbook_steps(bad)

    def test_conversion_refuses_step_without_provenance(self):
        bad_step = step("open settings")
        del bad_step["source_job_id"]
        bad = CompoundedWorkflow("bad", [bad_step], ["job-1"], ["bad"], [])
        with self.assertRaises(ValueError):
            compound_to_playbook_steps(bad)

    def test_conversion_refuses_step_without_proof(self):
        bad_step = step("open settings")
        bad_step["evidence_ref"] = {"proof_reference": "", "evidence": []}
        bad = CompoundedWorkflow("bad", [bad_step], ["job-1"], ["bad"], [])
        with self.assertRaises(ValueError):
            compound_to_playbook_steps(bad)

    def test_conversion_refuses_gated_step_without_gate(self):
        bad = CompoundedWorkflow(
            "bad", [step("send a note")], ["job-1"], ["bad"], [])
        with self.assertRaises(ValueError):
            compound_to_playbook_steps(bad)

    def test_conversion_refuses_forbidden_target(self):
        bad = CompoundedWorkflow(
            "bad", [step("read assistant.db")], ["job-1"], ["bad"], [])
        with self.assertRaises(ValueError):
            compound_to_playbook_steps(bad)

    def test_conversion_refuses_empty_compound(self):
        with self.assertRaises(ValueError):
            compound_to_playbook_steps(None)
        bad = CompoundedWorkflow("empty", [], [], [], [])
        with self.assertRaises(ValueError):
            compound_to_playbook_steps(bad)


if __name__ == "__main__":
    unittest.main()
