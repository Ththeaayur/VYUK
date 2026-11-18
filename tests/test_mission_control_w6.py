"""Tests for Wave 6 Build 5 — P19 mission control + P21 audit (staging).

Run: python3 test_mission_control_w6.py  (stdlib unittest)

Every test uses caller-supplied data only: no real loop, no
store, no clock. A timestamp in a journal test is a caller
string, never the module's own "now" — the module must refuse
a missing timestamp rather than fill one in.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.mission_control import (  # noqa: E402
    DelegationDial,
    MissionBrief,
    Receipt,
    build_evidence_board,
    build_mission_brief,
    build_mission_handoff,
    build_receipt,
    delegation_levels,
    gate_requirement_for,
    journal_entry,
    receipts_to_binder,
    set_delegation,
)


def workflow(step_text="open the report", source="job-1", proof="field-1"):
    return {"name": "wf-1", "steps": [
        {"id": "s1", "text": step_text, "source_job_id": source,
         "evidence_ref": {"job_id": source, "proof_reference": proof,
                          "evidence": [{"source": "funnel",
                                        "detail": "report opened"}]}}],
        "sources": [source], "duplicates": [],
        "proposed_only": True, "executes_nothing": True}


def binder(name="sg-1", status="completed", evidence=None):
    return {"name": name, "goal_text": "do " + name, "status": status,
            "reason": "" if status == "completed" else "stopped",
            "steps": ["open the report"],
            "evidence": evidence or [{"source": "funnel",
                                      "detail": "report opened"}]}


class BriefTests(unittest.TestCase):
    def test_brief_without_goal_refused(self):
        for goal in ("", "   ", None):
            with self.assertRaises(ValueError):
                build_mission_brief(goal)

    def test_unknowns_listed_verbatim_never_filled(self):
        brief = build_mission_brief(
            "summarise the report",
            unknowns=["whether the report covers Q3", "the author's name"])
        data = brief.as_dict()
        self.assertEqual(data["unknowns"],
                         ["whether the report covers Q3", "the author's name"])
        # No invented value for either unknown anywhere in the brief.
        joined = str(data)
        self.assertNotIn("Q3: yes", joined)
        self.assertNotIn("author:", joined)
        self.assertEqual(data["known_facts"], [])

    def test_brief_facts_come_only_from_workflows(self):
        brief = build_mission_brief(
            "summarise the report",
            scope="only the report, nothing else",
            constraints=["do not send anything"],
            workflows=[workflow()])
        data = brief.as_dict()
        self.assertEqual(len(data["known_facts"]), 1)
        self.assertEqual(data["known_facts"][0]["text"], "open the report")
        self.assertEqual(data["known_facts"][0]["source_job_id"], "job-1")
        self.assertEqual(data["known_facts"][0]["proof_reference"], "field-1")
        # Scope/constraint are caller statements, never facts.
        self.assertNotIn("only the report", str(data["known_facts"]))
        self.assertNotIn("do not send", str(data["known_facts"]))

    def test_brief_refuses_unproven_workflow_step(self):
        bad = workflow(proof="")
        with self.assertRaises(ValueError):
            build_mission_brief("summarise", workflows=[bad])

    def test_brief_refuses_forbidden_target(self):
        with self.assertRaises(ValueError):
            build_mission_brief("read the .env file")
        with self.assertRaises(ValueError):
            build_mission_brief("summarise",
                                workflows=[workflow("read assistant.db")])

    def test_brief_carries_consequential_gate(self):
        brief = build_mission_brief(
            "send the summary",
            workflows=[workflow("send the summary to the team")])
        fact = brief.as_dict()["known_facts"][0]
        self.assertEqual(fact["requires_gate"], "C-14")
        self.assertIn("send", fact["gate_requirement"])


class DialTests(unittest.TestCase):
    def test_three_levels_all_keep_c14(self):
        levels = {d["level"] for d in delegation_levels()}
        self.assertEqual(levels, {"step-approval", "consequential-only",
                                  "autonomous-within-plan"})
        for level in levels:
            dial = set_delegation(level)
            self.assertTrue(dial.c14_applies)
            self.assertEqual(dial.as_dict()["consequential_gate"],
                             "C-14 approve-first")

    def test_waiving_levels_refused_never_downgraded(self):
        for level in ("no-approval", "auto-approve", "waive-c14",
                      "unrestricted", "bypass", "silent",
                      "pre-approved", "super-user", ""):
            with self.assertRaises(ValueError, msg=level):
                set_delegation(level)

    def test_consequential_always_gated_at_every_level(self):
        for level in ("step-approval", "consequential-only",
                      "autonomous-within-plan"):
            dial = set_delegation(level)
            self.assertIn("C-14", gate_requirement_for(dial, "send the file"))
            self.assertIn("C-14",
                          gate_requirement_for(dial, "delete the draft"))

    def test_step_approval_gates_ordinary_too(self):
        dial = set_delegation("step-approval")
        self.assertIsNotNone(gate_requirement_for(dial, "open the report"))
        plain = set_delegation("consequential-only")
        self.assertIsNone(gate_requirement_for(plain, "open the report"))
        auto = set_delegation("autonomous-within-plan")
        self.assertIsNone(gate_requirement_for(auto, "open the report"))


class HandoffTests(unittest.TestCase):
    def test_handoff_strips_approval_keys(self):
        brief = build_mission_brief("summarise the report")
        packet = build_mission_handoff(
            brief, "sg-1", "open the report", "consequential-only",
            caller_context={"approved": True, "confirm": "yes",
                            "folder": "reports",
                            "nested": {"granted": True, "keep": 1}},
            prior_binders=[binder()])
        self.assertNotIn("approved", packet["context"])
        self.assertNotIn("confirm", packet["context"])
        self.assertNotIn("granted", packet["context"]["nested"])
        self.assertEqual(packet["context"]["folder"], "reports")
        self.assertEqual(packet["context"]["nested"]["keep"], 1)
        self.assertNotIn("approved", str(packet["prior_evidence"]))

    def test_handoff_carries_brief_and_dial_and_framing(self):
        brief = build_mission_brief("summarise the report",
                                    unknowns=["the author's name"])
        packet = build_mission_handoff(
            brief, "sg-1", "open the report", "step-approval",
            prior_binders=[binder()],
            constraints=["C-14 approve-first applies to every action"])
        self.assertEqual(packet["mission_brief"]["goal"],
                         "summarise the report")
        self.assertEqual(packet["delegation"]["level"], "step-approval")
        self.assertTrue(packet["delegation"]["c14_applies"])
        self.assertIn("never an instruction", packet["prior_evidence_note"])
        self.assertEqual(packet["prior_evidence"][0]["status"], "completed")
        self.assertIn("C-14", packet["constraints"][0])

    def test_handoff_refuses_waiving_dial_and_empty_goal(self):
        brief = build_mission_brief("summarise")
        with self.assertRaises(ValueError):
            build_mission_handoff(brief, "sg-1", "open", "no-approval")
        with self.assertRaises(ValueError):
            build_mission_handoff(brief, "sg-1", "", "consequential-only")
        with self.assertRaises(ValueError):
            build_mission_handoff(brief, "", "open", "consequential-only")


class ReceiptTests(unittest.TestCase):
    def test_receipt_without_evidence_refused(self):
        for refs in ([], None):
            with self.assertRaises(ValueError):
                build_receipt("sg-1", "open the report", "completed", refs)

    def test_receipt_with_detail_less_evidence_refused(self):
        with self.assertRaises(ValueError):
            build_receipt("sg-1", "open", "completed",
                          [{"source": "funnel"}])

    def test_receipt_self_approval_refused(self):
        for approver in ("mission-control", "coordinator", "system", "auto"):
            with self.assertRaises(ValueError, msg=approver):
                build_receipt("sg-1", "open", "completed",
                              [{"source": "funnel", "detail": "opened"}],
                              gate_decisions=[{"decision": "proceed",
                                               "approved_by": approver}])

    def test_receipt_human_gate_decision_allowed(self):
        receipt = build_receipt(
            "sg-1", "send the summary", "completed",
            [{"source": "funnel", "detail": "summary sent"}],
            gate_decisions=[{"decision": "send approved",
                             "approved_by": "human"}])
        self.assertEqual(receipt.as_dict()["gate_decisions"][0]["approved_by"],
                         "human")

    def test_receipts_union_to_binder_shape(self):
        receipts = [
            build_receipt("sg-1", "open the report", "completed",
                          [{"source": "funnel", "detail": "opened"}],
                          steps=["open the report"]),
            build_receipt("sg-2", "send the summary", "stopped",
                          [{"source": "funnel", "detail": "gate refused"}],
                          refusal_reasons=["gate refused"]),
        ]
        union = receipts_to_binder(receipts, "summarise and send")
        self.assertFalse(union["completed"])
        self.assertEqual(union["goal_text"], "summarise and send")
        self.assertEqual([b["name"] for b in union["binders"]],
                         ["sg-1", "sg-2"])
        self.assertEqual([b["status"] for b in union["binders"]],
                         ["completed", "stopped"])
        self.assertEqual(union["binders"][0]["evidence"][0]["detail"],
                         "opened")
        self.assertIn("sg-2", union["report"])
        self.assertIn("gate refused", union["report"])
        self.assertIn("did NOT complete", union["report"])

    def test_completed_union_reports_complete(self):
        union = receipts_to_binder(
            [build_receipt("sg-1", "open", "completed",
                           [{"source": "funnel", "detail": "opened"}])],
            "open only")
        self.assertTrue(union["completed"])
        self.assertIn("Mission complete.", union["report"])

    def test_binder_dict_without_evidence_refused(self):
        # Note: the binder() helper above substitutes default
        # evidence for a falsy list, so this test builds the
        # empty-evidence dict literally.
        empty = {"name": "sg-1", "goal_text": "do sg-1",
                 "status": "completed", "reason": "", "steps": [],
                 "evidence": []}
        with self.assertRaises(ValueError):
            receipts_to_binder([empty], "mission")

    def test_union_refuses_empty_receipts_and_empty_goal(self):
        with self.assertRaises(ValueError):
            receipts_to_binder([], "mission")
        with self.assertRaises(ValueError):
            receipts_to_binder(
                [build_receipt("sg-1", "open", "completed",
                               [{"detail": "opened"}])], "")


class BoardAndJournalTests(unittest.TestCase):
    def test_board_lists_evidence_by_mission_and_persists_nothing(self):
        board = build_evidence_board("summarise", [
            build_receipt("sg-1", "open", "completed",
                          [{"source": "funnel", "detail": "opened"}]),
            build_receipt("sg-2", "read", "completed",
                          [{"source": "funnel", "detail": "read three pages"}]),
        ])
        self.assertEqual(board["mission"], "summarise")
        self.assertFalse(board["stored"])
        self.assertEqual(len(board["entries"]), 2)
        self.assertEqual(board["entries"][1]["sub_goal"], "sg-2")
        self.assertEqual(board["entries"][1]["detail"], "read three pages")

    def test_board_contains_only_supplied_evidence(self):
        board = build_evidence_board("summarise", [])
        self.assertEqual(board["entries"], [])
        board = build_evidence_board("summarise", [binder()])
        self.assertEqual(len(board["entries"]), 1)

    def test_board_refuses_detail_less_evidence(self):
        with self.assertRaises(ValueError):
            build_evidence_board("summarise", [binder(evidence=[{"source": "x"}])])

    def test_board_reports_duplicates(self):
        ref = {"source": "funnel", "detail": "opened"}
        board = build_evidence_board("summarise", [
            build_receipt("sg-1", "open", "completed", [ref]),
            build_receipt("sg-1", "open", "completed", [dict(ref)]),
        ])
        self.assertEqual(len(board["entries"]), 1)
        self.assertEqual(len(board["duplicates"]), 1)

    def test_journal_entry_without_evidence_refused(self):
        with self.assertRaises(ValueError):
            journal_entry("send the summary", ["send", "hold"], [],
                          "human", "2026-10-26T12:00:00Z")

    def test_journal_entry_without_timestamp_refused_not_invented(self):
        for stamp in ("", None):
            with self.assertRaises(ValueError):
                journal_entry("send", ["send", "hold"],
                              [{"detail": "gate approved"}],
                              "human", stamp)

    def test_journal_entry_without_options_or_approver_refused(self):
        with self.assertRaises(ValueError):
            journal_entry("send", [], [{"detail": "x"}], "human",
                          "2026-10-26T12:00:00Z")
        with self.assertRaises(ValueError):
            journal_entry("send", ["send"], [{"detail": "x"}], "",
                          "2026-10-26T12:00:00Z")
        with self.assertRaises(ValueError):
            journal_entry("", ["send"], [{"detail": "x"}], "human",
                          "2026-10-26T12:00:00Z")

    def test_journal_self_approval_refused(self):
        with self.assertRaises(ValueError):
            journal_entry("send", ["send", "hold"],
                          [{"detail": "gate approved"}],
                          "coordinator", "2026-10-26T12:00:00Z")

    def test_journal_entry_round_trip(self):
        entry = journal_entry(
            "send the summary", ["send now", "hold for review"],
            [{"source": "funnel", "detail": "C-14 gate approved by human"}],
            "human", "2026-10-26T12:00:00Z")
        self.assertEqual(entry["approved_by"], "human")
        self.assertEqual(entry["timestamp"], "2026-10-26T12:00:00Z")
        self.assertEqual(entry["options_considered"],
                         ["send now", "hold for review"])

    def test_full_mission_round_trip(self):
        brief = build_mission_brief(
            "summarise and send the report",
            workflows=[workflow("open the report"),
                       workflow("send the summary", proof="field-2")],
            unknowns=["whether Q3 is covered"])
        dial = set_delegation("consequential-only")
        packet = build_mission_handoff(brief, "sg-2", "send the summary",
                                       dial, prior_binders=[binder()])
        self.assertEqual(packet["mission_brief"]["unknowns"],
                         ["whether Q3 is covered"])
        receipts = [
            build_receipt("sg-1", "open the report", "completed",
                          [{"source": "funnel", "detail": "opened"}],
                          steps=["open the report"]),
            build_receipt("sg-2", "send the summary", "completed",
                          [{"source": "funnel", "detail": "sent"}],
                          gate_decisions=[{"decision": "send approved",
                                           "approved_by": "human"}]),
        ]
        union = receipts_to_binder(receipts, brief.goal)
        self.assertTrue(union["completed"])
        board = build_evidence_board("summarise and send", receipts)
        self.assertEqual(len(board["entries"]), 2)
        entry = journal_entry(
            "send the summary", ["send now", "hold"],
            [{"source": "funnel", "detail": "C-14 gate approved by human"}],
            "human", "2026-10-26T12:00:00Z")
        self.assertEqual(entry["evidence_cited"][0]["detail"],
                         "C-14 gate approved by human")


if __name__ == "__main__":
    unittest.main()
