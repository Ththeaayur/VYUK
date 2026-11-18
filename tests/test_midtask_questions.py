"""Mid-task clarifying questions (B15, 2026-11-07): the coordinator
pauses with a named question instead of guessing.

Gap proved first (from the tree): zero ask_user/clarification
machinery existed in core/brain/ (only licence-clarification
strings in core/brain/plan_verify.py:20). The only pauses were
binary allow/deny (confirm gate, plan approval) plus the R5
monitor pause and the B14 budget pause — a task that hit an
ambiguity ("which file?", "send to whom?") had no way to ASK:
it guessed silently or halted the whole goal on one missing
detail.

What this batch builds (and this suite proves):

* ONE design: a clarifying question is a FOURTH pause shape
  in the same family — _pending_question, beside
  _pending_approval / _pending_monitor / the B14 budget
  pause. A step declares its question in its "ask" mapping
  (question + optional options + answer_key); the coordinator
  pauses at the step boundary before ANYTHING about the step
  runs, the human answers through the GUI pause surface, and
  resolve_question records the answer and re-runs the step
  with it injected into args. No default, no silent fallback,
  no confidence-threshold guess.
* Convergence is N=1: an answered question is never re-asked
  — the answer is ledger-serialized (unlike the one-shot
  approval flag, because an answer is information, not
  permission), so a rebuilt task resumes past it.
* An unanswered question leaves the task paused — never
  auto-answered, never burning budget or strikes. The
  dialog's 60 s silence is a timeout, never an answer.
* C-14: the answer channel is the GUI, never voice.

Fakes only: stub executors, a temp ledger, no real GUI
except the dialog tests (Qt-only, skipped on the system
interpreter), no real cloud calls.
"""

import pathlib
import tempfile
import unittest

from core.brain import task_coordinator as tc
from core.brain import task_ledger as tl

try:
    from PySide6.QtWidgets import QApplication
    _HAS_QT = True
except Exception:  # noqa: BLE001 — no PySide6 on sys
    _HAS_QT = False


def _ok(**kw):
    def _run(tool, args):
        _run.calls.append((tool, dict(args)))
        return {"status": "success", "kind": None, "result": "done"}
    _run.calls = []
    return _run


def _ask_step(tool, question, options=None, answer_key=None, label=None):
    ask = {"question": question}
    if options is not None:
        ask["options"] = options
    if answer_key is not None:
        ask["answer_key"] = answer_key
    step = {"tool": tool, "args": {}, "ask": ask}
    if label is not None:
        step["label"] = label
    return step


class MidtaskQuestionTests(unittest.TestCase):
    def _coord(self, tmp, executor=None):
        ledger = tl.TaskLedger(path=pathlib.Path(tmp) / "ledger.json")
        return tc.TurnCoordinator(executor=executor or _ok(),
                                  ledger=ledger), ledger

    def _task(self, coord, steps):
        return coord.new_task(
            "t", "saved_plan",
            [tc.StepDef.from_mapping(m) for m in steps])

    def test_question_pauses_with_record_and_no_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            coord, ledger = self._coord(tmp)
            task = self._task(coord, [
                _ask_step("write_file", "Which file should I write to?",
                          options=["notes.txt", "todo.txt"],
                          answer_key="filename", label="write the file"),
                {"tool": "read_file", "args": {}},
            ])
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "paused")
            self.assertEqual(task.outcomes, [])
            self.assertEqual(task._index, 0)
            pending = coord.pending_question(task.id)
            self.assertIsNotNone(pending)
            self.assertEqual(pending["question"],
                             "Which file should I write to?")
            self.assertEqual(pending["options"],
                             ["notes.txt", "todo.txt"])
            self.assertEqual(pending["step_index"], 0)
            self.assertEqual(pending["tool"], "write_file")
            self.assertEqual(pending["answer_key"], "filename")
            self.assertIn("Which file should I write to?",
                          receipt.reason)
            # The pause is in the ledger record, not just memory.
            record = ledger.get(task.id)
            self.assertIsNotNone(record)
            self.assertEqual(record.get("state"), "paused")
            self.assertEqual(
                record.get("pending_question", {}).get("question"),
                "Which file should I write to?")
            self.assertEqual(
                record.get("pending_question", {}).get("options"),
                ["notes.txt", "todo.txt"])
            # Nothing was guessed: no answer anywhere.
            self.assertEqual(task._question_answers, [])

    def test_answer_resumes_with_recorded_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            coord, ledger = self._coord(tmp, executor=executor)
            task = self._task(coord, [
                _ask_step("write_file", "Which file?",
                          answer_key="filename", label="write"),
            ])
            coord.run_task(task)
            self.assertEqual(task.state, "paused")
            receipt = coord.resolve_question(task.id, "notes.txt")
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.state, "completed")
            # The answer fed the step, under its answer_key.
            self.assertEqual(len(executor.calls), 1)
            self.assertEqual(executor.calls[0][1].get("filename"),
                             "notes.txt")
            # The step's own args were never mutated — the
            # injection landed on the dispatch copy only.
            self.assertNotIn("filename", task.steps[0].args)
            # The answer is in the receipt and the ledger.
            self.assertEqual(len(receipt.questions), 1)
            entry = receipt.questions[0]
            self.assertEqual(entry["question"], "Which file?")
            self.assertEqual(entry["answer"], "notes.txt")
            self.assertIsNone(entry["option"])
            record = ledger.get(task.id)
            self.assertEqual(len(record.get("question_answers") or []), 1)
            self.assertEqual(
                record["question_answers"][0]["answer"], "notes.txt")
            self.assertIsNone(record.get("pending_question"))

    def test_options_record_which_was_chosen(self):
        with tempfile.TemporaryDirectory() as tmp:
            coord, _ledger = self._coord(tmp)
            task = self._task(coord, [
                _ask_step("open_file", "Which file?",
                          options=["a.txt", "b.txt"], label="open"),
            ])
            coord.run_task(task)
            receipt = coord.resolve_question(task.id, "b.txt")
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(receipt.questions[0]["option"], "b.txt")
            self.assertEqual(receipt.questions[0]["answer"], "b.txt")
        with tempfile.TemporaryDirectory() as tmp:
            coord, _ledger = self._coord(tmp)
            task = self._task(coord, [
                _ask_step("open_file", "Which file?",
                          options=["a.txt", "b.txt"], label="open"),
            ])
            coord.run_task(task)
            # Free text when options exist is still an answer —
            # recorded with option None, never forced into one.
            receipt = coord.resolve_question(task.id, "c.txt")
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(receipt.questions[0]["answer"], "c.txt")
            self.assertIsNone(receipt.questions[0]["option"])

    def test_question_never_reasked_after_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            coord, ledger = self._coord(tmp, executor=executor)
            task = self._task(coord, [
                _ask_step("write_file", "Which file first?",
                          answer_key="filename", label="first"),
                _ask_step("write_file", "Which file second?",
                          answer_key="filename", label="second"),
            ])
            coord.run_task(task)
            self.assertEqual(
                coord.pending_question(task.id)["question"],
                "Which file first?")
            coord.resolve_question(task.id, "one.txt")
            # Paused on the SECOND question now — the first
            # was never re-asked.
            self.assertEqual(task.state, "paused")
            self.assertEqual(
                coord.pending_question(task.id)["question"],
                "Which file second?")
            self.assertEqual(len(task._question_answers), 1)
            # Crash/restart convergence: rebuild the paused
            # record and resume — it must pause on the second
            # question directly, never re-ask the first.
            record = ledger.get(task.id)
            coord2, _ledger2 = self._coord(tmp, executor=executor)
            receipt2 = coord2.resume_ledger_task(record, ledger=ledger)
            self.assertIsNotNone(receipt2)
            self.assertEqual(receipt2.state, "paused")
            rebuilt = coord2.get_task(task.id)
            self.assertEqual(
                coord2.pending_question(task.id)["question"],
                "Which file second?")
            self.assertEqual(len(rebuilt._question_answers), 1)
            self.assertEqual(rebuilt._question_answers[0]["answer"],
                             "one.txt")
            coord2.resolve_question(task.id, "two.txt")
            self.assertEqual(rebuilt.state, "completed")
            self.assertEqual(
                [c[1].get("filename") for c in executor.calls],
                ["one.txt", "two.txt"])

    def test_empty_answer_resolves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            coord, _ledger = self._coord(tmp)
            task = self._task(coord, [
                _ask_step("write_file", "Which file?",
                          answer_key="filename"),
            ])
            coord.run_task(task)
            for bad in ("", "   ", None, 42):
                self.assertIsNone(coord.resolve_question(task.id, bad))
                self.assertEqual(task.state, "paused")
                self.assertIsNotNone(coord.pending_question(task.id))
                self.assertEqual(task.outcomes, [])
                self.assertEqual(task._question_answers, [])
                # No strike, no budget burn: the step never
                # ran, and the pause went through the normal
                # run_task exit (the B14 window closed).
                self.assertEqual(task._jump_strikes, {})
                self.assertIsNone(task._budget_started_at)

    def test_explicit_arg_wins_no_question_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            coord, _ledger = self._coord(tmp, executor=executor)
            step = _ask_step("write_file", "Which file?",
                             answer_key="filename")
            step["args"] = {"filename": "already-known.txt"}
            task = self._task(coord, [step])
            receipt = coord.run_task(task)
            self.assertEqual(receipt.state, "completed")
            self.assertIsNone(coord.pending_question(task.id))
            self.assertEqual(executor.calls[0][1].get("filename"),
                             "already-known.txt")
            self.assertEqual(task._question_answers, [])

    def test_malformed_ask_rejected_at_build(self):
        bads = [
            {"tool": "a", "ask": "which file?"},
            {"tool": "a", "ask": {}},
            {"tool": "a", "ask": {"question": ""}},
            {"tool": "a", "ask": {"question": "   "}},
            {"tool": "a", "ask": {"question": 42}},
            {"tool": "a", "ask": {"question": "q?", "options": "a"}},
            {"tool": "a", "ask": {"question": "q?", "options": [""]}},
            {"tool": "a", "ask": {"question": "q?", "options": [1]}},
            {"tool": "a", "ask": {"question": "q?", "answer_key": 3}},
        ]
        for bad in bads:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    tc.StepDef.from_mapping(bad)
        # An empty answer_key defaults to "answer", like a
        # missing one — it is not malformed.
        self.assertEqual(
            tc.StepDef.from_mapping(
                {"tool": "a", "ask": {"question": "q?",
                                     "answer_key": ""}}).ask["answer_key"],
            "answer")
        # Well-formed asks build (defaults included).
        good = tc.StepDef.from_mapping(
            {"tool": "a", "ask": {"question": "Which?"}})
        self.assertEqual(good.ask["question"], "Which?")
        self.assertEqual(good.ask["options"], [])
        self.assertEqual(good.ask["answer_key"], "answer")
        good2 = tc.StepDef.from_mapping(
            {"tool": "a", "ask": {"question": "Which?",
                                 "options": ["x", "x", "y"],
                                 "answer_key": "pick"}})
        self.assertEqual(good2.ask["options"], ["x", "y"])
        self.assertEqual(good2.ask["answer_key"], "pick")

    def test_cancel_clears_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            coord, ledger = self._coord(tmp)
            task = self._task(coord, [
                _ask_step("write_file", "Which file?"),
            ])
            coord.run_task(task)
            self.assertTrue(coord.cancel_task(task.id))
            self.assertEqual(task.state, "cancelled")
            self.assertIsNone(task._pending_question)
            self.assertIsNone(coord.pending_question(task.id))
            record = ledger.get(task.id)
            self.assertEqual(record.get("state"), "cancelled")
            self.assertIsNone(record.get("pending_question"))

    def test_pending_question_accessor_is_a_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            coord, _ledger = self._coord(tmp)
            task = self._task(coord, [
                _ask_step("write_file", "Which file?"),
            ])
            coord.run_task(task)
            pending = coord.pending_question(task.id)
            pending["question"] = "tampered?"
            self.assertEqual(
                coord.pending_question(task.id)["question"],
                "Which file?")
            self.assertIsNone(coord.pending_question("no-such-task"))
            self.assertIsNone(coord.resolve_question("no-such-task", "x"))
            # Resolving a completed task resolves nothing.
            coord.resolve_question(task.id, "notes.txt")
            self.assertEqual(task.state, "completed")
            self.assertIsNone(coord.resolve_question(task.id, "again.txt"))

    def test_ask_survives_ledger_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            coord, ledger = self._coord(tmp)
            task = self._task(coord, [
                _ask_step("write_file", "Which file?",
                          options=["a.txt"], answer_key="filename"),
            ])
            coord.run_task(task)
            record = ledger.get(task.id)
            self.assertEqual(
                record["steps"][0]["ask"]["question"], "Which file?")
            self.assertEqual(
                record["steps"][0]["ask"]["options"], ["a.txt"])
            self.assertEqual(
                record["steps"][0]["ask"]["answer_key"], "filename")
            rebuilt = tl.TaskLedger(
                path=pathlib.Path(tmp) / "ledger.json").rebuild_task(
                    record)
            self.assertEqual(rebuilt.steps[0].ask["question"],
                             "Which file?")
            self.assertEqual(
                rebuilt._pending_question["question"], "Which file?")

    def test_gui_surface_helpers_pure(self):
        from gui.pending_question import (
            interrupted_pending_questions, pending_question_summary)
        tool, text = pending_question_summary({
            "tool": "write_file", "label": "write",
            "question": "Which file?",
            "options": ["a.txt", "b.txt"]})
        self.assertEqual(tool, "write_file")
        self.assertIn("Which file?", text)
        self.assertIn("a.txt", text)
        self.assertEqual(pending_question_summary({})[0], "")
        queue = interrupted_pending_questions([
            {"task_id": "t1", "pending_question": {
                "question": "Which file?", "tool": "write_file"}},
            {"task_id": "t2", "pending_question": None},
            {"task_id": "t3", "pending_question": {}},
            {"pending_question": {"question": "No id?"}},
            {"task_id": "t4", "pending_question": {
                "question": "Send to whom?", "tool": "send_email"}},
        ])
        self.assertEqual([t for t, _ in queue], ["t1", "t4"])

    def test_activity_renders_needs_an_answer(self):
        from gui.task_state import render_task
        record = {
            "task_id": "t1", "title": "t", "state": "paused",
            "steps": [{"tool": "write_file", "label": "write"}],
            "outcomes": [],
            "pending_question": {
                "question": "Which file?", "tool": "write_file",
                "label": "write"},
        }
        rendered = render_task(record)
        self.assertEqual(rendered["state"], "Needs an answer")
        # A paused-for-approval task still renders Needs
        # approval — the question state never steals it.
        record2 = dict(record)
        record2["pending_question"] = None
        record2["pending_approval"] = {
            "tool": "send_email", "label": "send"}
        self.assertEqual(render_task(record2)["state"],
                         "Needs approval")

    def test_resolve_question_answer_worker_path(self):
        from gui.pending_question import resolve_question_answer
        with tempfile.TemporaryDirectory() as tmp:
            executor = _ok()
            coord, ledger = self._coord(tmp, executor=executor)
            task = self._task(coord, [
                _ask_step("write_file", "Which file?",
                          answer_key="filename"),
            ])
            coord.run_task(task)
            # Empty answers resolve nothing anywhere.
            self.assertIsNone(resolve_question_answer(
                task.id, "", live_coordinator=coord))
            self.assertIsNone(resolve_question_answer(
                task.id, None, live_coordinator=coord))
            self.assertEqual(task.state, "paused")
            receipt = resolve_question_answer(
                task.id, "notes.txt", live_coordinator=coord)
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.state, "completed")
            self.assertEqual(executor.calls[0][1].get("filename"),
                             "notes.txt")
            # A stale answer (no pending question) resolves nothing.
            self.assertIsNone(resolve_question_answer(
                task.id, "again.txt", live_coordinator=coord))
            self.assertIsNone(resolve_question_answer(
                "no-such-task", "x", live_coordinator=coord))

    def test_no_voice_path_into_resolve_question(self):
        # C-14: the answer channel is the GUI, never voice.
        # The question module must not reference any voice
        # resolution path — a spoken answer must have nowhere
        # to land. (The word "voice" appears in the module
        # docstring's boundary note only as a prohibition.)
        root = pathlib.Path(__file__).resolve().parent.parent
        text = (root / "gui" / "pending_question.py").read_text()
        for forbidden in ("voice_goal", "voice_text_bridge",
                          "_maybe_confirm_reply", "voice_client",
                          "live_client"):
            self.assertNotIn(forbidden, text)
        # And no voice-side module calls resolve_question.
        for path in list((root / "core" / "brain").glob("voice*.py")) \
                + list((root / "gui").glob("window_services*.py")):
            self.assertNotIn(
                "resolve_question", path.read_text(),
                f"{path.name} must not resolve questions")


@unittest.skipUnless(_HAS_QT, "no PySide6 on the system interpreter")
class QuestionDialogTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication([])
        from PySide6.QtWidgets import QWidget
        self.window = QWidget()

    def tearDown(self):
        try:
            self.window.close()
        except Exception:  # noqa: BLE001 — teardown never raises
            pass

    def test_dialog_renders_question_and_options(self):
        from gui.pending_question import build_question_dialog
        dlg = build_question_dialog(self.window, {
            "tool": "write_file", "label": "write the file",
            "question": "Which file should I write to?",
            "options": ["notes.txt", "todo.txt"],
            "answer_key": "filename"})
        self.assertEqual(
            len(dlg._question_option_buttons), 2)
        self.assertEqual(
            dlg._question_option_buttons[0].text(), "notes.txt")
        self.assertIsNotNone(dlg._question_input)
        self.assertIsNotNone(dlg._question_btn_answer)

    def test_option_click_answers_with_that_option(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog
        from gui.pending_question import present_question_dialog
        pending = {"tool": "write_file", "label": "write",
                   "question": "Which file?",
                   "options": ["notes.txt", "todo.txt"]}
        result = {}

        def _click():
            for widget in QApplication.topLevelWidgets():
                if hasattr(widget, "_question_option_buttons"):
                    widget._question_option_buttons[1].click()
                    return

        QTimer.singleShot(50, _click)
        result.update(present_question_dialog(
            self.window, pending, timeout_s=30))
        self.assertEqual(result["outcome"], "answered")
        self.assertEqual(result["answer"], "todo.txt")

    def test_free_text_answer_button(self):
        from PySide6.QtCore import QTimer
        from gui.pending_question import present_question_dialog
        pending = {"tool": "write_file", "label": "write",
                   "question": "Which file?", "options": []}
        result = {}

        def _answer():
            for widget in QApplication.topLevelWidgets():
                if hasattr(widget, "_question_input"):
                    widget._question_input.setText("custom.txt")
                    widget._question_btn_answer.click()
                    return

        QTimer.singleShot(50, _answer)
        result.update(present_question_dialog(
            self.window, pending, timeout_s=30))
        self.assertEqual(result["outcome"], "answered")
        self.assertEqual(result["answer"], "custom.txt")

    def test_silence_timeout_is_never_an_answer(self):
        from gui.pending_question import present_question_dialog
        pending = {"tool": "write_file", "label": "write",
                   "question": "Which file?",
                   "options": ["notes.txt"]}
        result = present_question_dialog(
            self.window, pending, timeout_s=0.02)
        self.assertEqual(result["outcome"], "timeout")
        self.assertIsNone(result["answer"])

    def test_empty_answer_button_does_not_accept(self):
        from PySide6.QtWidgets import QDialog
        from gui.pending_question import build_question_dialog
        dlg = build_question_dialog(self.window, {
            "tool": "write_file", "label": "write",
            "question": "Which file?", "options": []})
        # An empty Answer click must not accept the dialog —
        # Enter on an empty field cannot invent an answer.
        # (accept() sets the result even without exec, so the
        # result is the proof: still Rejected, never Accepted.)
        dlg._question_btn_answer.click()
        self.assertNotEqual(dlg.result(), QDialog.DialogCode.Accepted)
        # With text, the same button accepts.
        dlg._question_input.setText("notes.txt")
        dlg._question_btn_answer.click()
        self.assertEqual(dlg.result(), QDialog.DialogCode.Accepted)
        dlg.close()


if __name__ == "__main__":
    unittest.main()
