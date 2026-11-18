"""Second-opinion auto-invoke (library-sweep decision item, closed here).

The explicit opt-in second_opinion function (core/second_opinion.py +
core/w8_wiring.py request_second_opinion) is wired into automatic invocation
for high-stakes coordinator steps. Trigger, question, and verdict mapping are
decided in the coordinator (see _second_opinion_check's docstring + the
batch design note): narrow (destructive-first-word + side-effecting only),
question built from label/menu-leaf only (never args), fail-closed toward a
human decision — only YES/YES agreement proceeds, everything else pauses with
both views, and brains never silently approve or silently veto.

Stdlib unittest only. Executors are stubs; the ledger tests use temp paths,
never the live data/ store. Brain callables are injected fakes with call
counts, so no-loop and no-recheck claims are proven by counting, not by
assuming.
"""

import tempfile
import unittest
from pathlib import Path

from core.brain import task_coordinator as tc  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
from core.brain.task_ledger import TaskLedger  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/


def _ok_executor():
    def run(tool, args):
        run.calls.append((tool, dict(args)))
        return {"status": "success", "kind": None, "result": "done"}
    run.calls = []
    return run


def _coord(executor=None, brains=None, ledger=None):
    return tc.TurnCoordinator(
        executor=executor or _ok_executor(),
        second_opinion_brains=brains, ledger=ledger)


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


def _ledger():
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "ledger.json"
    _ledger._dirs.append(tmp)
    return TaskLedger(path)


_ledger._dirs = []


class TriggerTests(unittest.TestCase):
    def test_high_stakes_step_invokes_and_proceeds_on_yes_yes(self):
        yes_a, yes_b = _counting("YES"), _counting("yes")
        executor = _ok_executor()
        coord = _coord(executor=executor, brains=(yes_a, yes_b))
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(len(yes_a.calls), 1)
        self.assertEqual(len(yes_b.calls), 1)
        self.assertEqual(len(receipt.second_opinions), 1)
        event = receipt.second_opinions[0]
        self.assertEqual(event["verdict"], "agree")
        self.assertEqual(event["action"], "proceeded")
        self.assertIn("Delete old logs", event["question"])
        self.assertNotIn("secret", event["question"])

    def test_low_stakes_label_not_invoked(self):
        yes_a, yes_b = _counting("YES"), _counting("YES")
        coord = _coord(brains=(yes_a, yes_b))
        task = _task(coord, [{"tool": "send_report", "args": {},
                              "label": "List weekly reports"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(yes_a.calls, [])
        self.assertEqual(yes_b.calls, [])
        self.assertEqual(receipt.second_opinions, [])

    def test_save_label_not_invoked(self):
        # "Save" is deliberately absent from the destructive set.
        yes_a, yes_b = _counting("YES"), _counting("YES")
        coord = _coord(brains=(yes_a, yes_b))
        task = _task(coord, [{"tool": "write_file", "args": {},
                              "label": "Save the report"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(yes_a.calls, [])
        self.assertEqual(receipt.second_opinions, [])

    def test_readonly_tool_not_invoked_despite_destructive_label(self):
        # The trigger is a conjunction: destructive word AND
        # side-effecting tool. A read-only tool never qualifies.
        yes_a, yes_b = _counting("YES"), _counting("YES")
        coord = _coord(brains=(yes_a, yes_b))
        task = _task(coord, [{"tool": "list_plans", "args": {},
                              "label": "Delete old plans"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(yes_a.calls, [])
        self.assertEqual(receipt.second_opinions, [])

    def test_menu_list_not_invoked(self):
        yes_a, yes_b = _counting("YES"), _counting("YES")
        coord = _coord(brains=(yes_a, yes_b))
        task = _task(coord, [{"tool": "menu_action",
                              "args": {"action": "list", "path": ["File"]},
                              "label": "menu list"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(yes_a.calls, [])
        self.assertEqual(receipt.second_opinions, [])

    def test_menu_invoke_destructive_leaf_invokes(self):
        yes_a, yes_b = _counting("YES"), _counting("YES")
        executor = _ok_executor()
        coord = _coord(executor=executor, brains=(yes_a, yes_b))
        task = _task(coord, [{"tool": "menu_action",
                              "args": {"action": "invoke",
                                       "path": ["File", "Delete"]},
                              "label": "menu delete"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(len(yes_a.calls), 1)
        event = receipt.second_opinions[0]
        self.assertEqual(event["verdict"], "agree")
        self.assertIn("Delete", event["question"])

    def test_menu_invoke_routine_leaf_not_invoked(self):
        yes_a, yes_b = _counting("YES"), _counting("YES")
        coord = _coord(brains=(yes_a, yes_b))
        task = _task(coord, [{"tool": "menu_action",
                              "args": {"action": "invoke",
                                       "path": ["File", "Open"]},
                              "label": "menu open"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(yes_a.calls, [])
        self.assertEqual(receipt.second_opinions, [])

    def test_when_skipped_step_never_checked(self):
        # The check sits after the when-probe: a step skipped by
        # its probe never reaches the check, however destructive.
        def _probe_fail(tool, args):
            _probe_fail.calls.append((tool, dict(args)))
            return {"status": "failure", "kind": "probe",
                    "message": "probe failed"}
        _probe_fail.calls = []
        yes_a, yes_b = _counting("YES"), _counting("YES")
        coord = _coord(executor=_probe_fail, brains=(yes_a, yes_b))
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs",
                              "on_fail": "skip",
                              "when": {"tool": "p", "args": {}}}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(receipt.skipped, 1)
        self.assertEqual(yes_a.calls, [])
        self.assertEqual(receipt.second_opinions, [])

    def test_question_never_carries_args(self):
        # Args may carry user data; the question is built from
        # the label only. The brains see WHAT runs, never its data.
        seen = []
        def _spy(question):
            seen.append(question)
            return "YES"
        coord = _coord(brains=(_spy, _spy))
        task = _task(coord, [{"tool": "send_email",
                              "args": {"to": "boss@example.com",
                                       "body": "secret plan"},
                              "label": "Send the report"}])
        coord.run_task(task)
        self.assertEqual(len(seen), 2)
        for question in seen:
            self.assertNotIn("boss@example.com", question)
            self.assertNotIn("secret plan", question)


class VerdictTests(unittest.TestCase):
    def test_agreement_changes_nothing_but_record(self):
        yes_a, yes_b = _counting("YES"), _counting("YES")
        executor = _ok_executor()
        coord = _coord(executor=executor, brains=(yes_a, yes_b))
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(len(receipt.second_opinions), 1)
        text = tc.format_receipt(receipt)
        self.assertIn("Second opinion", text)
        self.assertIn("agree", text)
        self.assertIn("proceeded", text)

    def test_disagreement_pauses_with_both_views_and_approves_to_run(self):
        yes_brain, no_brain = _counting("YES"), _counting("NO")
        executor = _ok_executor()
        coord = _coord(executor=executor, brains=(yes_brain, no_brain))
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "paused")
        # The step never ran: the pause precedes dispatch.
        self.assertEqual(executor.calls, [])
        pending = coord.pending_approval(task.id)
        self.assertIsNotNone(pending)
        self.assertIn("YES", pending["sentence"])
        self.assertIn("NO", pending["sentence"])
        self.assertEqual(pending["confirm_arg"], "")
        # Approval re-runs the step WITHOUT re-checking it: each
        # brain was consulted exactly once across the whole cycle.
        receipt = coord.resolve_pending(task.id, approved=True)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(len(yes_brain.calls), 1)
        self.assertEqual(len(no_brain.calls), 1)
        self.assertEqual(len(receipt.second_opinions), 1)
        self.assertEqual(receipt.second_opinions[0]["verdict"], "disagree")
        self.assertEqual(receipt.second_opinions[0]["action"], "paused")

    def test_disagreement_denied_refuses_honestly(self):
        coord = _coord(brains=(_counting("YES"), _counting("NO")))
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "paused")
        receipt = coord.resolve_pending(task.id, approved=False)
        self.assertEqual(receipt.state, "failed")
        step = receipt.steps[0]
        self.assertEqual(step["status"], "refused")
        self.assertEqual(step["kind"], "approval_denied")
        # The refusal record carries both views (the sentence).
        self.assertIn("YES", step["message"])
        self.assertIn("NO", step["message"])

    def test_both_no_pauses(self):
        # Agreement on a NON-confirmation is not a confirmation:
        # two brains saying NO must not silently proceed, and
        # must not silently veto either — the human decides.
        coord = _coord(brains=(_counting("NO"), _counting("NO")))
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "paused")
        self.assertEqual(receipt.second_opinions[0]["verdict"], "agree")
        self.assertEqual(receipt.second_opinions[0]["action"], "paused")
        receipt = coord.resolve_pending(task.id, approved=True)
        self.assertEqual(receipt.state, "completed")

    def test_identical_non_yes_pauses(self):
        coord = _coord(brains=(_counting("Maybe"), _counting("maybe")))
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "paused")
        self.assertEqual(receipt.second_opinions[0]["verdict"], "agree")
        self.assertEqual(receipt.second_opinions[0]["action"], "paused")

    def test_formatting_variant_disagrees_pauses(self):
        # Exact comparison is the module's design: "YES" vs
        # "Yes, proceed" disagree, and the coordinator must not
        # fuzz them into agreement behind the module's back.
        coord = _coord(brains=(_counting("YES"),
                                _counting("Yes, proceed")))
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "paused")
        self.assertEqual(receipt.second_opinions[0]["verdict"], "disagree")

    def test_unavailable_brain_pauses(self):
        coord = _coord(brains=(_counting("YES"),
                                _counting(RuntimeError("boom"))))
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "paused")
        self.assertEqual(receipt.second_opinions[0]["verdict"], "unavailable")
        pending = coord.pending_approval(task.id)
        self.assertIn("no second opinion could be formed",
                      pending["sentence"])
        receipt = coord.resolve_pending(task.id, approved=True)
        self.assertEqual(receipt.state, "completed")

    def test_no_brains_configured_is_off(self):
        # Not-configured is OFF, not unavailable: no check, no
        # pause, no record — behaviour exactly as before.
        executor = _ok_executor()
        coord = _coord(executor=executor)
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(receipt.second_opinions, [])

    def test_non_callable_brains_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            _coord(brains=("not-a-brain", _counting("YES")))
        with self.assertRaises(ValueError):
            _coord(brains=_counting("YES"))
        with self.assertRaises(ValueError):
            _coord(brains=(_counting("YES"), _counting("NO"),
                           _counting("YES")))

    def test_check_crash_pauses_once_then_proceeds(self):
        # White-box: the constructor rejects bad pairs, so a
        # crashing check is forced here by mutating the pair
        # after construction. The check must pause once (never
        # break the loop, never silently pass), and the recorded
        # error event must gate any re-check on resume.
        yes_brain = _counting("YES")
        executor = _ok_executor()
        coord = _coord(executor=executor, brains=(yes_brain, yes_brain))
        coord._second_opinion_brains = (None, yes_brain)
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "paused")
        self.assertEqual(receipt.second_opinions[0]["verdict"], "error")
        self.assertEqual(executor.calls, [])
        receipt = coord.resolve_pending(task.id, approved=True)
        self.assertEqual(receipt.state, "completed")
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(len(receipt.second_opinions), 1)


class RecordingTests(unittest.TestCase):
    def test_history_in_ledger_and_rebuild(self):
        ledger = _ledger()
        executor = _ok_executor()
        coord = _coord(executor=executor,
                       brains=(_counting("YES"), _counting("NO")),
                       ledger=ledger)
        task = _task(coord, [{"tool": "delete_file", "args": {},
                              "label": "Delete old logs"}])
        receipt = coord.run_task(task)
        self.assertEqual(receipt.state, "paused")
        record = ledger.get(task.id)
        self.assertEqual(len(record["second_opinions"]), 1)
        self.assertEqual(record["second_opinions"][0]["verdict"], "disagree")
        # The pending record itself keeps the existing shape.
        self.assertEqual(set(record["pending_approval"].keys()),
                         {"step_index", "tool", "label", "sentence",
                          "confirm_arg"})
        # Rebuild from the paused record: the history restores.
        rebuilt = TaskLedger().rebuild_task(record)
        self.assertEqual(len(rebuilt._second_opinions), 1)
        self.assertEqual(rebuilt._second_opinions[0]["verdict"], "disagree")
        receipt = coord.resolve_pending(task.id, approved=True)
        self.assertEqual(receipt.state, "completed")
        record = ledger.get(task.id)
        # No duplicate event: the resume did not re-check.
        self.assertEqual(len(record["second_opinions"]), 1)

    def test_old_ledger_record_without_key_loads_empty(self):
        ledger = _ledger()
        coord = _coord(ledger=ledger)
        task = _task(coord, [{"tool": "a", "args": {}}])
        coord.run_task(task)
        record = ledger.get(task.id)
        del record["second_opinions"]
        rebuilt = TaskLedger().rebuild_task(
            {**record, "state": "paused"})
        self.assertEqual(rebuilt._second_opinions, [])


class ExplicitOptInTests(unittest.TestCase):
    def test_explicit_opt_in_direct_call_still_works(self):
        # The explicit path is untouched by auto-invoke wiring.
        from core import w8_wiring
        agree = w8_wiring.request_second_opinion(
            "Is the sky blue?",
            brain_a=_counting("YES"), brain_b=_counting("yes"))
        self.assertEqual(agree["status"], "agree")
        self.assertIsNone(agree["escalation"])
        self.assertIsNone(agree["resolved_answer"])
        disagree = w8_wiring.request_second_opinion(
            "Is the sky blue?",
            brain_a=_counting("YES"), brain_b=_counting("NO"))
        self.assertEqual(disagree["status"], "disagree")
        self.assertIsNotNone(disagree["escalation"])
        self.assertEqual(disagree["escalation"]["answer_a"], "YES")
        self.assertEqual(disagree["escalation"]["answer_b"], "NO")
        self.assertIsNone(disagree["resolved_answer"])
        missing = w8_wiring.request_second_opinion("Is the sky blue?")
        self.assertEqual(missing["status"], "unavailable")
        self.assertIn("trigger_owed", missing)

    def test_module_contract_pinned(self):
        # What the wiring relies on: agree/disagree/unavailable,
        # no auto-resolution, no certainty language in UI strings.
        from core import second_opinion
        agree = second_opinion.cross_check(
            "q", _counting("YES"), _counting("yes"))
        self.assertEqual(agree.verdict, "agree")
        self.assertFalse(agree.escalate)
        self.assertIsNone(agree.resolved_answer)
        disagree = second_opinion.cross_check(
            "q", _counting("YES"), _counting("NO"))
        self.assertEqual(disagree.verdict, "disagree")
        self.assertTrue(disagree.escalate)
        self.assertIsNone(disagree.resolved_answer)
        self.assertIsNotNone(
            second_opinion.escalation_payload(disagree))
        self.assertIsNone(second_opinion.escalation_payload(agree))
        unavailable = second_opinion.cross_check(
            "q", _counting("YES"), _counting(RuntimeError("boom")))
        self.assertEqual(unavailable.verdict, "unavailable")
        self.assertTrue(unavailable.escalate)
        rendered = second_opinion.render_for_ui(disagree)
        joined = " ".join(str(v) for v in rendered.values()).lower()
        for word in ("verified", "certain", "guaranteed"):
            self.assertNotIn(word, joined)


if __name__ == "__main__":
    unittest.main()
