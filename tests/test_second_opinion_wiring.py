"""Second-opinion wiring batch tests (owner ruling 2026-11-05).

This file pins what THIS batch changed, not what the auto-invoke
batch already proved (tests/test_second_opinion_autoinvoke.py covers
the trigger matrix, verdict mapping, and ledger round-trip):

1. Marker retirement: SECOND_OPINION_TRIGGER_OWED no longer says the
   trigger is owed/unruled — it records the ruled conjunction, the
   wired coordinator auto-invoke, and the honest production status
   (check OFF in production until brain-pair plumbing lands at a
   coordinator construction site).
2. Pause-sentence word check: the human-facing pause sentences (the
   S-1 dialog surface) never claim verified/certain/guaranteed —
   the auto-invoke word check covers render_for_ui only, not these
   sentences, so this file closes that gap.
3. No resolved answer anywhere in the coordinator record: pause and
   proceed events carry no resolved_answer key, so no brain answer
   is ever picked for the human.

Stdlib unittest only. Executors are stubs; brain callables are
injected fakes with call counts. No live data/ store is touched.
"""

import unittest
from types import SimpleNamespace

from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.brain.task_coordinator_opinion import _second_opinion_pause_sentence


def _ok_executor():
    def run(tool, args):
        run.calls.append((tool, dict(args)))
        return {"status": "success", "kind": None, "result": "done"}
    run.calls = []
    return run


def _coord(executor=None, brains=None):
    return tc.TurnCoordinator(
        executor=executor or _ok_executor(),
        second_opinion_brains=brains)


def _task(coord, steps):
    return coord.new_task(
        "t", "saved_plan",
        [tc.StepDef.from_mapping(m) for m in steps])


def _counting(answer):
    """A brain fake returning `answer` (or raising it, if an exception)."""
    def brain(question):
        brain.calls.append(question)
        if isinstance(answer, BaseException):
            raise answer
        return answer
    brain.calls = []
    return brain


_BANNED_WORDS = ("verified", "certain", "guaranteed")


def _fake_step():
    return SimpleNamespace(label="Delete old logs", tool="delete_file")


class MarkerRetirementTests(unittest.TestCase):
    def test_marker_says_ruled_not_owed(self):
        # INTENTIONAL (second-opinion wiring batch): the owner ruled
        # the trigger 2026-11-05, so the marker must record the ruling
        # instead of saying the decision is still owed.
        from core.w8_wiring_gates import SECOND_OPINION_TRIGGER_OWED
        self.assertIn("RULED 2026-11-05", SECOND_OPINION_TRIGGER_OWED)
        lowered = SECOND_OPINION_TRIGGER_OWED.lower()
        self.assertNotIn("until the owner rules", lowered)
        self.assertNotIn("still owed", lowered)

    def test_opt_in_docstring_no_longer_says_owed(self):
        # INTENTIONAL (second-opinion wiring batch): the two other
        # places that said the trigger was owed now say ruled.
        from core import w8_wiring, w8_wiring_opinion
        self.assertIn("RULED 2026-11-05",
                      w8_wiring_opinion.request_second_opinion.__doc__)
        self.assertNotIn("trigger is OWED",
                         w8_wiring_opinion.request_second_opinion.__doc__)
        self.assertNotIn("still owed", w8_wiring.__doc__)

    def test_unavailable_still_carries_trigger_key(self):
        # Contract stability: the unavailable return keeps its
        # "trigger_owed" key (an existing test pins the key), but the
        # value is now the retired marker text, not an owed claim.
        from core import w8_wiring
        missing = w8_wiring.request_second_opinion("Is the sky blue?")
        self.assertEqual(missing["status"], "unavailable")
        self.assertIn("trigger_owed", missing)
        self.assertIn("RULED 2026-11-05", missing["trigger_owed"])


class RulingConjunctionPinTests(unittest.TestCase):
    # INTENTIONAL (second-opinion wiring batch): this class pins the
    # OWNER RULING of 2026-11-05 in one place — a step triggers second
    # opinion ONLY when BOTH a destructive word AND a side-effecting
    # tool hold. It deliberately overlaps TriggerTests in
    # test_second_opinion_autoinvoke.py: those prove the trigger
    # mechanism, these pin the ruling to it.

    def test_destructive_plus_side_effecting_fires(self):
        yes_a, yes_b = _counting("YES"), _counting("yes")
        coord = _coord(brains=(yes_a, yes_b))
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(yes_a.calls), 1)
        self.assertEqual(len(yes_b.calls), 1)
        self.assertEqual(len(receipt.second_opinions), 1)

    def test_destructive_plus_readonly_does_not_fire(self):
        # The ruling's standing case: list_plans + "Delete old plans"
        # is NOT invoked — the read-only half alone never qualifies.
        yes_a, yes_b = _counting("YES"), _counting("YES")
        coord = _coord(brains=(yes_a, yes_b))
        task = _task(coord, [{"tool": "list_plans", "args": {},
                              "label": "Delete old plans"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(yes_a.calls, [])
        self.assertEqual(yes_b.calls, [])
        self.assertEqual(receipt.second_opinions, [])

    def test_benign_label_plus_side_effecting_does_not_fire(self):
        yes_a, yes_b = _counting("YES"), _counting("YES")
        coord = _coord(brains=(yes_a, yes_b))
        task = _task(coord, [{"tool": "send_report", "args": {},
                              "label": "List weekly reports"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(yes_a.calls, [])
        self.assertEqual(receipt.second_opinions, [])

    def test_menu_list_does_not_fire(self):
        yes_a, yes_b = _counting("YES"), _counting("YES")
        coord = _coord(brains=(yes_a, yes_b))
        task = _task(coord, [{"tool": "menu_action",
                              "args": {"action": "list", "path": ["File"]},
                              "label": "menu list"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(yes_a.calls, [])
        self.assertEqual(receipt.second_opinions, [])


class PauseSentenceWordTests(unittest.TestCase):
    def test_pause_sentences_never_claim_certainty(self):
        # The pause sentence IS the human-facing surface (the S-1
        # dialog renders it verbatim), so it must never claim
        # verified/certain/guaranteed. The auto-invoke word check
        # covers render_for_ui only — this closes the pause gap.
        step = _fake_step()
        variants = [
            SimpleNamespace(verdict="disagree", answer_a="YES",
                            answer_b="NO", disagreement=""),
            SimpleNamespace(verdict="unavailable", answer_a="",
                            answer_b="",
                            disagreement="no live brain sessions."),
            SimpleNamespace(verdict="agree", answer_a="NO",
                            answer_b="NO", disagreement=""),
        ]
        for result in variants:
            sentence = _second_opinion_pause_sentence(step, result)
            lowered = sentence.lower()
            for word in _BANNED_WORDS:
                self.assertNotIn(word, lowered)
            self.assertIn("Allow once", sentence)
            self.assertIn("Don't allow", sentence)
        disagree = _second_opinion_pause_sentence(step, variants[0])
        self.assertIn("Neither answer was picked", disagree)

    def test_end_to_end_pause_sentence_never_claims_certainty(self):
        executor = _ok_executor()
        coord = _coord(executor=executor,
                       brains=(_counting("YES"), _counting("NO")))
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "paused")
        self.assertEqual(executor.calls, [])
        pending = coord.pending_approval(task.id)
        self.assertIsNotNone(pending)
        lowered = pending["sentence"].lower()
        for word in _BANNED_WORDS:
            self.assertNotIn(word, lowered)


class NoResolvedAnswerTests(unittest.TestCase):
    def test_coordinator_events_carry_no_resolved_answer(self):
        # Neither a pause nor a proceed ever picks a brain answer for
        # the human: no coordinator event carries a resolved_answer
        # key at all.
        executor = _ok_executor()
        coord = _coord(executor=executor,
                       brains=(_counting("YES"), _counting("NO")))
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "paused")
        self.assertEqual(len(receipt.second_opinions), 1)
        self.assertNotIn("resolved_answer",
                         receipt.second_opinions[0])
        receipt = coord.resolve_pending(task.id, approved=True)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(len(receipt.second_opinions), 1)
        self.assertNotIn("resolved_answer",
                         receipt.second_opinions[0])

    def test_proceeded_event_carries_no_resolved_answer(self):
        coord = _coord(brains=(_counting("YES"), _counting("yes")))
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(receipt.second_opinions), 1)
        self.assertEqual(receipt.second_opinions[0]["action"],
                         "proceeded")
        self.assertNotIn("resolved_answer",
                         receipt.second_opinions[0])


if __name__ == "__main__":
    unittest.main()
