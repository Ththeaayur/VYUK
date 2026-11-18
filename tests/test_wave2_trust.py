"""Wave 2 trust-slice tests (stdlib unittest, no GUI, no network).

Covers P13 track record / capability map / lessons / pre-flight,
P19 delegation dial / escalation ladder, P21 sitrep / blast radius
/ because-tags / decision journal, P13-16 + P17-18 skill review.
"""

import unittest

from core import w2_trust as trust


class TrackRecordTests(unittest.TestCase):
    def test_counts_only_recorded_jobs(self):
        tr = trust.TrackRecord()
        tr.append("j1", "complete")
        tr.append("j2", "halted")
        tr.append("j3", "complete")
        s = tr.summary()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["by_outcome"]["complete"], 2)


class CapabilityMapTests(unittest.TestCase):
    def test_proven_pilot_gated(self):
        cm = trust.CapabilityMap()
        self.assertTrue(cm.set("voice_bridge", "pilot", evidence="w1")["ok"])
        self.assertEqual(cm.get("voice_bridge")["status"], "pilot")
        self.assertEqual(cm.get("missing")["status"], "unknown")

    def test_unknown_status_refused(self):
        cm = trust.CapabilityMap()
        self.assertFalse(cm.set("x", "magic")["ok"])


class LessonsTests(unittest.TestCase):
    def test_lessons_from_outcomes_only(self):
        result = trust.lessons_learned([{"lesson": "revalidate before reuse"},
                                        {"outcome": "complete"}])
        self.assertEqual(result["lessons"], ["revalidate before reuse"])


class PreflightTests(unittest.TestCase):
    def test_all_pass_not_blocked(self):
        r = trust.preflight_check(failsafe_ok=True, target_resolved=True,
                                  confirm_gate_ok=True, scope_ok=True)
        self.assertFalse(r["blocked"])

    def test_any_fail_blocks_with_list(self):
        r = trust.preflight_check(failsafe_ok=False, target_resolved=True,
                                  confirm_gate_ok=True, scope_ok=True)
        self.assertTrue(r["blocked"])
        self.assertIn("failsafe", r["failing"])


class DelegationTests(unittest.TestCase):
    def test_dial_never_overrides_consequential(self):
        for dial in ("suggest", "confirm", "act"):
            d = trust.delegation_decision(dial, "send")
            self.assertEqual(d["decision"], "ask")

    def test_act_dial_acts_on_plain_action(self):
        d = trust.delegation_decision("act", "open_app")
        self.assertEqual(d["decision"], "act")

    def test_suggest_dial_suggests(self):
        d = trust.delegation_decision("suggest", "open_app")
        self.assertEqual(d["decision"], "suggest")


class EscalationTests(unittest.TestCase):
    def test_escalates_one_level_never_acts(self):
        r = trust.escalation_step("self", "tool failed twice")
        self.assertEqual(r["level"], "ask_owner")
        self.assertFalse(r["action_taken"])

    def test_unknown_level_fails_closed(self):
        r = trust.escalation_step("nonsense", "x")
        self.assertEqual(r["level"], "stop_and_report")


class SitrepTests(unittest.TestCase):
    def test_no_data_yet_not_guessed(self):
        self.assertIn("no data yet", trust.sitrep([]))

    def test_lines_from_logs_only(self):
        s = trust.sitrep(["job j1 complete", "job j2 halted"])
        self.assertIn("job j1 complete", s)
        self.assertIn("job j2 halted", s)


class BlastRadiusTests(unittest.TestCase):
    def test_scoped_steps(self):
        r = trust.blast_radius([{"window": "Mail"}, {"file": "notes.txt"}])
        self.assertEqual(r["windows"], ["Mail"])
        self.assertFalse(r["unknown"])

    def test_unscoped_step_is_unknown(self):
        r = trust.blast_radius([{"tool": "something"}])
        self.assertTrue(r["unknown"])


class BecauseTagTests(unittest.TestCase):
    def test_tag_requires_reason(self):
        self.assertFalse(trust.because_tag("proactive tip", "")["tagged"])
        tagged = trust.because_tag("proactive tip", "job j1 just completed")
        self.assertTrue(tagged["tagged"])


class JournalTests(unittest.TestCase):
    def test_append_only(self):
        j = trust.DecisionJournal()
        j.append("allow open_app", "non-consequential", "done")
        self.assertEqual(len(j.all()), 1)
        self.assertEqual(j.all()[0]["decision"], "allow open_app")


class SkillReviewTests(unittest.TestCase):
    def test_plain_words(self):
        s = trust.skill_review_plain({"name": "Mail helper",
                                      "what_it_does": "opens mail",
                                      "status": "pilot"})
        self.assertIn("Mail helper", s)
        self.assertIn("opens mail", s)

    def test_unknown_stays_unknown(self):
        s = trust.skill_review_plain({})
        self.assertIn("unknown", s)


if __name__ == "__main__":
    unittest.main()
