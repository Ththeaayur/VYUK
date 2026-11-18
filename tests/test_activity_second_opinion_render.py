"""Activity second-opinion rendering: the GUI shows a task's
second-opinion history in the same Details view that already
shows its verification and retry lines.

Ground truth: the second-opinion batch stores history in the
ledger record ("second_opinions" key, core/brain/task_ledger.py)
and the receipt text prints it (format_receipt,
core/brain/task_coordinator.py) — but gui/task_state.render_task
never rendered it, so Activity showed no second-opinion lines
anywhere. This suite pins the rendering through that existing
detail seam, mirroring the retries render batch exactly:
checked task -> second-opinion lines present, worded exactly as
the receipt words them and in the receipt's order (after the
Retry lines); unchecked task -> no section at all (matching
format_receipt's only-when-non-empty rule). No new panel, no
new dialog: the lines live in details_text, which the row's
Details button already opens.

The visual proof is ~/workspace/render_activity_second_opinion.py,
which saves ~/workspace/code-lab/activity_second_opinion_render.png:
the expanded Details dialog for a checked task showing its
second-opinion lines. MainWindow itself is NOT driven here
(window_memory.py is unimportable in the lab without PortAudio
— the standing lab limit); the Details dialog content is the
render_task details_text the row's Details button carries, and
the row widget itself is exercised below.

Stdlib unittest only for the mapping tests; the Qt row-widget
test needs PySide6 (skipped where absent, like the other GUI
suites).
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui import task_state  # noqa: E402


_MISSING = object()


def _record(second_opinions=_MISSING, **over):
    record = {
        "task_id": "checked-task",
        "title": "Checked task",
        "state": "completed",
        "steps": [{"tool": "menu_action", "label": "Delete file",
                   "args": {}}],
        "outcomes": [{"tool": "menu_action", "label": "Delete file",
                      "status": "success"}],
        "verification": [{"label": "Delete file", "verdict": "met",
                          "reason": "file gone"}],
        "retries": [{"step_index": 0, "tool": "menu_action",
                     "label": "Delete file", "verdict": "met",
                     "reason": "file gone", "strike": 0,
                     "retried": False}],
        "updated_at": "2026-11-03T10:00:00+00:00",
    }
    if second_opinions is not _MISSING:
        record["second_opinions"] = second_opinions
    record.update(over)
    return record


CHECKED = [
    {"step_index": 0, "tool": "menu_action",
     "label": "Delete file", "verdict": "agree", "action": "proceeded",
     "reason": "", "answer_a": "YES", "answer_b": "YES",
     "question": "Answer with one word, YES or NO: is it safe and "
                "correct for the assistant to invoke the menu "
                "item 'Delete'?"},
    {"step_index": 0, "tool": "menu_action",
     "label": "Delete file", "verdict": "disagree", "action": "paused",
     "reason": "", "answer_a": "YES", "answer_b": "NO",
     "question": "Answer with one word, YES or NO: is it safe and "
                "correct for the assistant to invoke the menu "
                "item 'Delete'?"},
    {"step_index": 0, "tool": "menu_action",
     "label": "Delete file", "verdict": "unavailable",
     "action": "paused", "reason": "", "answer_a": "",
     "answer_b": "", "question": "Answer with one word, YES or NO: "
                "is it safe and correct for the assistant to "
                "invoke the menu item 'Delete'?"},
]


class TestSecondOpinionDetails(unittest.TestCase):
    def test_checked_task_shows_lines_receipt_wording(self):
        details = task_state.render_task(_record(CHECKED))["details_text"]
        # Receipt wording (format_receipt), minus the receipt's
        # two-space list indent — details_text drops that indent
        # for Verification lines already, and does the same here.
        self.assertIn(
            "Second opinion: Delete file \u2014 agree (proceeded)",
            details)
        self.assertIn(
            "Second opinion: Delete file \u2014 disagree (paused)",
            details)
        self.assertIn(
            "Second opinion: Delete file \u2014 unavailable (paused)",
            details)

    def test_lines_follow_verification_and_retry_lines(self):
        details = task_state.render_task(_record(CHECKED))["details_text"]
        self.assertLess(details.index("Verification: Delete file"),
                        details.index("Second opinion: Delete file"))
        self.assertLess(details.index("Retry: Delete file"),
                        details.index("Second opinion: Delete file"))

    def test_lines_before_remaining(self):
        # Two steps, one outcome: the second step is still
        # remaining, so a Remaining line follows the history.
        record = _record(
            CHECKED,
            state="paused",
            steps=[{"tool": "menu_action", "label": "Delete file",
                    "args": {}},
                   {"tool": "menu_action", "label": "Empty trash",
                    "args": {}}])
        details = task_state.render_task(record)["details_text"]
        self.assertIn("Remaining: Empty trash", details)
        self.assertLess(details.index("Second opinion: Delete file"),
                        details.index("Remaining: Empty trash"))

    def test_unchecked_task_no_section(self):
        # No "second_opinions" key at all (pre-second-opinion
        # ledger records).
        details = task_state.render_task(_record())["details_text"]
        self.assertNotIn("Second opinion:", details)

    def test_empty_no_section(self):
        details = task_state.render_task(_record([]))["details_text"]
        self.assertNotIn("Second opinion:", details)

    def test_malformed_ignored_not_crash(self):
        # A non-list value must never crash the row, and adds
        # no second-opinion lines.
        details = task_state.render_task(
            _record("not-a-list"))["details_text"]
        self.assertNotIn("Second opinion:", details)
        # Non-dict entries are skipped; a partial dict renders
        # with the receipt's defaults (empty verdict, empty
        # action).
        details = task_state.render_task(
            _record(["not-a-dict", {"label": "Delete file"}])
            )["details_text"]
        self.assertIn("Second opinion: Delete file \u2014  ()", details)

    def test_other_details_unchanged(self):
        details = task_state.render_task(_record(CHECKED))["details_text"]
        self.assertIn("Verification: Delete file \u2014 met", details)
        self.assertIn("Retry: Delete file \u2014 met (strike 0) \u2014 halted",
                      details)
        self.assertIn("Task id: checked-task", details)


class TestSecondOpinionRowWidget(unittest.TestCase):
    def test_row_widget_still_builds_for_checked_task(self):
        try:
            from PySide6.QtWidgets import QApplication, QLabel
        except ImportError:
            self.skipTest("PySide6 absent")
        app = QApplication.instance() or QApplication([])
        rendered = task_state.render_task(_record(CHECKED))
        widget = task_state.build_task_row_widget(rendered)
        labels = [lbl.text() for lbl in widget.findChildren(QLabel)]
        self.assertIn("Checked task", labels)
        self.assertIn("Done", labels)
        # The row's Details button carries the full details_text
        # (with second-opinion lines) to the dialog — the button
        # exists and the rendered details hold the lines.
        self.assertIn("Second opinion: Delete file", rendered["details_text"])


if __name__ == "__main__":
    unittest.main()
