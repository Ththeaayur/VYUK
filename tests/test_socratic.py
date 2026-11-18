"""U5 — Socratic mode: the tutor asks instead of telling.

Lab leg only. The real-Windows field leg — a real MA 105 Socratic
session on his machine, hinted in order, never leaking the answer,
with "I don't know" dropping to the prerequisite — is OWED, not this
batch.

Gap proved first (audit T5): U3's hint is a single nudge
(``hint_for`` — the rubric's first clause, no ladder state); no
tutor policy file exists anywhere in the tree; no solver/speaker
split exists (``CheckSession`` holds the bite INCLUDING the gold
and its ``snapshot()`` serialises it — the same object that grades
also produces the replies).

What this suite proves: the ladder advances in order (L1 -> L2 ->
L3, never skips) and its state persists across turns within a check;
"show me" jumps to the bottom; "I don't know" drops to the
prerequisite (the returned back-track names it); bottom-out requires
"now explain why" (a correct answer after bottom-out without
teach-back does not count as a clean transfer); the speaker never
receives the gold (no reply, no speaker attribute, no metadata key
carries it); the policy file is human-editable (a policy change
changes behaviour without a code change); the spoken escalation
protocol ("hint" / "show me" / "I don't know") costs no graded
attempt; no ladder reply reveals the gold (including when the
rubric and the bite text themselves contain the gold value, and
when a caller-supplied variant does); a ladder hint before a
correct answer means no clean transfer (the proxy seam); the
deterministic fail-safe never guesses a grade; the retry bound
hands to diagnosis; the snapshot round-trip.
"""

import os
import tempfile
import unittest
from pathlib import Path

from core.brain import socratic as so
from core.brain import student_model as sm
from core.brain.lesson_planner import Bite
from core.memory import MemoryStore

SKILL = "ma105-double-integrals-limits-from-region"
REINFORCE = "ma105-regions-and-limits"
GOLD = "7/3"
RUBRIC = ("The answer must show the integrated value from "
          "integrating term by term. The limits must follow the "
          "region, not the usual range.")
BITE_TEXT = ("Integrate term by term: the integral of a sum is "
             "the sum of the integrals, each term on its own.")
CHECK = "What is the value of the integral?"

# A bite whose rubric AND bite text contain the gold value — the
# guards must fall back to generic lines, never leak it.
LEAKY_RUBRIC = ("The answer must show 7/3 from integrating term "
                "by term. The limits must follow the region.")
LEAKY_BITE = ("Integrate term by term to get 7/3: the integral "
              "of a sum is the sum of the integrals.")


def _tmp_store(testcase):
    tmp = Path(tempfile.mkdtemp(prefix="socratic_"))
    testcase.addCleanup(
        lambda: [os.unlink(p) for p in tmp.iterdir()] or tmp.rmdir())
    return MemoryStore(tmp / "assistant.db")


def _bite(**overrides):
    fields = {
        "skill": SKILL,
        "bite": BITE_TEXT,
        "check": CHECK,
        "gold": GOLD,
        "rubric": RUBRIC,
        "reinforce": REINFORCE,
        "requires": (REINFORCE,),
    }
    fields.update(overrides)
    return Bite(**fields)


def _variants():
    seen = []

    def variant_fn(bite, attempt):
        text = f"Variant {attempt}: what is the value now?"
        seen.append(text)
        return text

    return variant_fn, seen


def _session(testcase, bite=None, **kwargs):
    model = sm.StudentModel(_tmp_store(testcase))
    session = so.SocraticSession(bite or _bite(), model, **kwargs)
    return session, model


def _judge_true(text, gold, rubric):
    return {"correct": True, "deduction": ""}


class TestLadderOrder(unittest.TestCase):
    def test_levels_advance_in_order_never_skip(self):
        variant_fn, _ = _variants()
        session, _ = _session(self, variant_fn=variant_fn)
        session.start()
        solver = session._solver
        l1, l2, l3 = solver.hint_texts()
        r1 = session.respond("0.9")
        self.assertEqual(session.ladder_level, 1)
        self.assertIn(l1, r1["reply"])
        r2 = session.respond("hint")
        self.assertEqual(session.ladder_level, 2)
        self.assertIn(l2, r2["reply"])
        r3 = session.respond("hint")
        self.assertEqual(session.ladder_level, 3)
        self.assertIn(l3, r3["reply"])
        self.assertIn("explain why", r3["reply"])

    def test_ladder_state_persists_across_turns(self):
        variant_fn, _ = _variants()
        session, _ = _session(self, variant_fn=variant_fn)
        session.start()
        session.respond("0.9")
        self.assertEqual(session.ladder_level, 1)
        r = session.respond("hint")
        # Second hint climbs to L2 — it does not repeat L1.
        self.assertEqual(session.ladder_level, 2)
        self.assertIn("The principle this bite turns on is:",
                      r["reply"])
        self.assertIn(session._solver.hint_texts()[1], r["reply"])

    def test_show_me_jumps_straight_to_bottom(self):
        session, _ = _session(self)
        session.start()
        r = session.respond("show me")
        self.assertEqual(session.ladder_level, 3)
        self.assertTrue(session._bottomed_out)
        self.assertIn(session._solver.hint_texts()[2], r["reply"])
        self.assertIn("explain why", r["reply"])

    def test_wrong_answers_climb_the_ladder_too(self):
        variant_fn, _ = _variants()
        session, _ = _session(self, variant_fn=variant_fn)
        session.start()
        session.respond("0.9")
        self.assertEqual(session.ladder_level, 1)
        session.respond("0.8")
        self.assertEqual(session.ladder_level, 2)
        session.respond("0.7")
        # Third wrong answer hands to diagnosis (U3 bound).
        self.assertEqual(session.status, "done")
        self.assertEqual(session.outcome["outcome"],
                         "handed_to_diagnosis")


class TestEscalationProtocol(unittest.TestCase):
    def test_escalation_costs_no_graded_attempt(self):
        session, model = _session(self)
        session.start()
        session.respond("hint")
        session.respond("show me")
        self.assertEqual(session.status, "checking")
        self.assertEqual(model.get_skill(SKILL)["evidence"], 0)

    def test_dont_know_drops_to_prerequisite(self):
        session, model = _session(self)
        session.start()
        r = session.respond("I don't know")
        self.assertIsNotNone(r["backtrack"])
        self.assertEqual(r["backtrack"]["skill"], REINFORCE)
        self.assertTrue(r["backtrack"]["question"])
        self.assertEqual(session.status, "checking")
        # Nothing graded, nothing recorded.
        self.assertEqual(model.get_skill(SKILL)["evidence"], 0)

    def test_dont_know_falls_back_to_requires(self):
        bite = _bite(reinforce=SKILL, requires=("ma105:prereq-one",))
        session, _ = _session(self, bite=bite)
        session.start()
        r = session.respond("i don't know")
        self.assertEqual(r["backtrack"]["skill"], "ma105:prereq-one")

    def test_dont_know_uses_library_question_when_one_exists(self):
        session, _ = _session(self)
        session.start()
        r = session.respond("I don't know")
        # The seeded U4 library has diagnostic questions for the
        # ma105- family; the back-track carries one of them.
        question = r["backtrack"]["question"].lower()
        self.assertTrue("region" in question or "integrand" in question)


class TestTeachBack(unittest.TestCase):
    def _reach_bottom(self, testcase, **kwargs):
        variant_fn, _ = _variants()
        session, model = _session(testcase, variant_fn=variant_fn,
                                  **kwargs)
        session.start()
        session.respond("0.9")
        session.respond("hint")
        session.respond("hint")
        self.assertEqual(session.ladder_level, 3)
        return session, model

    def test_bottom_out_requires_teach_back(self):
        session, model = self._reach_bottom(self)
        r = session.respond(GOLD)
        self.assertEqual(session.status, "teach_back")
        self.assertIsNone(session.outcome)
        self.assertIn("explain why", r["reply"].lower())
        # No clean transfer without the explanation.
        self.assertEqual(model.get_skill(SKILL)["transfers"], 0)
        r2 = session.respond(
            "because we integrate term by term and the limits "
            "follow the region")
        self.assertEqual(session.status, "done")
        self.assertEqual(session.outcome["teach_back"], "passed")
        self.assertEqual(session.outcome["outcome"], "still_learning")

    def test_teach_back_failure_hands_to_diagnosis(self):
        session, _ = self._reach_bottom(self)
        session.respond(GOLD)
        self.assertEqual(session.status, "teach_back")
        session.respond("because it just works")
        self.assertEqual(session.status, "teach_back")
        r = session.respond("because magic")
        self.assertEqual(session.status, "done")
        self.assertEqual(session.outcome["outcome"],
                         "handed_to_diagnosis")
        self.assertEqual(session.outcome["reason"],
                         "teach_back_failed")

    def test_teach_back_judge_grades_the_explanation(self):
        session, _ = self._reach_bottom(
            self, judge=_judge_true)
        session.respond(GOLD)
        r = session.respond("any words at all")
        self.assertEqual(session.status, "done")
        self.assertEqual(session.outcome["teach_back"], "passed")

    def test_teach_back_off_in_policy(self):
        session, _ = self._reach_bottom(
            self, policy={"teach_back": False})
        r = session.respond(GOLD)
        self.assertEqual(session.status, "done")
        self.assertIsNotNone(session.outcome)
        self.assertNotIn("teach_back", session.outcome)


class TestSpeakerSplit(unittest.TestCase):
    def test_speaker_never_receives_the_gold(self):
        variant_fn, _ = _variants()
        session, _ = _session(self, variant_fn=variant_fn,
                              judge=_judge_true)
        session.start()
        replies = []
        replies.append(session.respond("0.9")["reply"])
        replies.append(session.respond("hint")["reply"])
        replies.append(session.respond("show me")["reply"])
        replies.append(session.respond("0.8")["reply"])
        replies.append(session.respond(GOLD)["reply"])
        replies.append(session.respond(
            "because we integrate term by term")["reply"])
        for reply in replies:
            self.assertNotIn(GOLD, reply)
        speaker = session._speaker
        self.assertFalse(hasattr(speaker, "gold"))
        self.assertNotIn(GOLD, str(vars(speaker)))

    def test_speaker_refuses_non_packets(self):
        speaker = so.Speaker()
        with self.assertRaises(TypeError):
            speaker.render("not a packet")
        with self.assertRaises(TypeError):
            speaker.render({"gold": GOLD, "correct": False})

    def test_metadata_has_exactly_three_keys(self):
        session, _ = _session(self)
        metadata = session._solver.metadata_from_grade(
            {"correct": False, "reason": "value does not match "
             "the gold", "deduction": "dropped the factor",
             "method": "numeric"}, hint="a hint")
        self.assertEqual(set(metadata),
                         {"is_correct", "error_location",
                          "hint_suggestion"})
        self.assertEqual(metadata["error_location"], "value")

    def test_no_ladder_reply_reveals_gold_when_bite_leaks(self):
        bite = _bite(bite=LEAKY_BITE, rubric=LEAKY_RUBRIC)
        variant_fn, _ = _variants()
        session, _ = _session(self, bite=bite, variant_fn=variant_fn)
        session.start()
        replies = []
        replies.append(session.respond("0.9")["reply"])
        replies.append(session.respond("hint")["reply"])
        replies.append(session.respond("show me")["reply"])
        replies.append(session.respond("0.8")["reply"])
        for reply in replies:
            self.assertNotIn(GOLD, reply)
        # The guards fell back to generic lines.
        self.assertIn("Look at what the answer has to show",
                      replies[0])

    def test_variant_containing_gold_falls_back(self):
        def leaky_variant(bite, attempt):
            return f"Variant {attempt}: is the answer {GOLD}?"

        session, _ = _session(self, variant_fn=leaky_variant)
        session.start()
        r = session.respond("0.9")
        self.assertNotIn(GOLD, r["reply"])
        self.assertIn(CHECK, r["reply"])


class TestPolicyFile(unittest.TestCase):
    def test_policy_change_changes_behaviour(self):
        tmp = Path(tempfile.mkdtemp(prefix="socratic_policy_"))
        self.addCleanup(lambda: [os.unlink(p) for p in tmp.iterdir()]
                        or tmp.rmdir())
        path = tmp / "TUTOR_POLICY.md"
        path.write_text(
            "# Policy\n\n```policy\n"
            "hint_word: nudge\n"
            "show_word: show me\n"
            "dont_know: i don't know\n"
            "teach_back: true\n```\n", encoding="utf-8")
        policy, source = so.load_policy(path)
        self.assertEqual(source, "file")
        self.assertEqual(policy["hint_word"], "nudge")
        session, model = _session(self, policy_path=path)
        session.start()
        r = session.respond("nudge")
        self.assertEqual(session.ladder_level, 1)
        self.assertEqual(model.get_skill(SKILL)["evidence"], 0)
        # "hint" is no longer an escalation word: it is graded.
        r2 = session.respond("hint")
        self.assertEqual(model.get_skill(SKILL)["evidence"], 1)

    def test_policy_defaults_when_file_missing(self):
        policy, source = so.load_policy(
            Path("/nonexistent/no-such-policy.md"))
        self.assertEqual(source, "default")
        self.assertEqual(policy, so.DEFAULT_POLICY)

    def test_default_policy_file_is_read(self):
        policy, source = so.load_policy()
        self.assertEqual(source, "file")
        self.assertEqual(policy["hint_word"], "hint")
        self.assertTrue(policy["teach_back"])


class TestMasteryHonesty(unittest.TestCase):
    def test_hint_then_correct_is_not_a_clean_transfer(self):
        session, model = _session(self)
        session.start()
        session.respond("hint")
        session.respond(GOLD)
        self.assertEqual(model.get_skill(SKILL)["transfers"], 0)
        self.assertEqual(model.get_skill(SKILL)["evidence"], 1)

    def test_hint_then_correct_note_claims_no_transfer(self):
        # Parent-found flaw: the record was honest (0 transfers)
        # but the spoken note claimed "one clean transfer".
        # The note must say the answer came with a hint, does
        # NOT count as a clean transfer, and state the true
        # count on record.
        session, model = _session(self)
        session.start()
        session.respond("hint")
        r = session.respond(GOLD)
        reply = r["reply"]
        self.assertNotIn("one clean transfer", reply.lower())
        self.assertIn("doesn't count as a clean transfer", reply)
        self.assertIn("0 clean transfers on record", reply)
        self.assertEqual(model.get_skill(SKILL)["transfers"], 0)
        self.assertEqual(r["outcome"]["mastery"]["transfers"], 0)

    def test_clean_correct_note_states_actual_transfers(self):
        session, model = _session(self)
        session.start()
        r = session.respond(GOLD)
        self.assertIn("1 clean transfer on record", r["reply"])
        self.assertIn("1 more on a fresh variant", r["reply"])
        self.assertEqual(model.get_skill(SKILL)["transfers"], 1)

    def test_correct_without_hint_is_a_clean_transfer(self):
        session, model = _session(self)
        session.start()
        session.respond(GOLD)
        self.assertEqual(model.get_skill(SKILL)["transfers"], 1)

    def test_two_clean_transfers_master(self):
        model = sm.StudentModel(_tmp_store(self))
        for _ in range(2):
            session = so.SocraticSession(_bite(), model)
            session.start()
            session.respond(GOLD)
        self.assertTrue(model.is_mastered(SKILL))

    def test_low_confidence_reasks_and_grades_nothing(self):
        session, model = _session(self)
        session.start()
        r = session.respond("0.9", confidence=0.3)
        self.assertIn("did you say '0.9'", r["reply"])
        self.assertEqual(session.status, "checking")
        self.assertEqual(model.get_skill(SKILL)["evidence"], 0)
        # A hearing that IS the gold is re-asked generically —
        # echoing it would confirm the answer.
        r2 = session.respond(GOLD, confidence=0.3)
        self.assertNotIn("did you say", r2["reply"])
        self.assertNotIn(GOLD, r2["reply"])
        self.assertEqual(model.get_skill(SKILL)["evidence"], 0)

    def test_fail_safe_judge_never_guesses_a_pass(self):
        def bad_judge(text, gold, rubric):
            return {"nope": 1}

        variant_fn, _ = _variants()
        session, model = _session(self, judge=bad_judge,
                                  variant_fn=variant_fn)
        session.start()
        r = session.respond("some words")
        self.assertEqual(session.status, "checking")
        self.assertEqual(session.ladder_level, 1)
        self.assertFalse(model.get_skill(SKILL)["correct"])


class TestSnapshot(unittest.TestCase):
    def test_snapshot_round_trips_a_pending_session(self):
        variant_fn, _ = _variants()
        session, model = _session(self, variant_fn=variant_fn)
        session.start()
        session.respond("0.9")
        self.assertEqual(session.ladder_level, 1)
        snap = session.snapshot()
        restored = so.SocraticSession.from_snapshot(
            snap, model, variant_fn=variant_fn)
        self.assertEqual(restored.ladder_level, 1)
        self.assertEqual(restored.status, "checking")
        r = restored.respond("hint")
        self.assertEqual(restored.ladder_level, 2)
        self.assertIn("principle", r["reply"].lower())
