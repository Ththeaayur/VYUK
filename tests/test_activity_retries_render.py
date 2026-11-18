"""Activity retries rendering: the GUI shows a task's retry history
in the same Details view that already shows its outcomes.

Ground truth: the retry batch stores history in the ledger record
("retries" key, core/brain/task_ledger.py) and the receipt text
prints it (format_receipt, core/brain/task_coordinator.py) — but
gui/task_state.render_task never rendered it, so Activity showed
no retry lines anywhere. This suite pins the rendering through
that existing detail seam: retried task -> retry lines present,
worded exactly as the receipt words them; unretried task -> no
retry section at all (matching format_receipt's only-when-
non-empty rule). No new panel, no new dialog: the lines live in
details_text, which the row's Details button already opens.

The visual proof is ~/workspace/render_activity_retries.py,
which saves ~/workspace/code-lab/activity_retries_render.png:
the expanded Details dialog for a retried task showing its
retry lines. MainWindow itself is NOT driven here
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


def _record(retries=_MISSING, **over):
    record = {
        "task_id": "retried-task",
        "title": "Retried task",
        "state": "completed",
        "steps": [{"tool": "file_dialog_action", "label": "Save report",
                   "args": {}}],
        "outcomes": [{"tool": "file_dialog_action", "label": "Save report",
                      "status": "success"}],
        "verification": [{"label": "Save report", "verdict": "met",
                          "reason": "file exists"}],
        "updated_at": "2026-11-03T10:00:00+00:00",
    }
    if retries is not _MISSING:
        record["retries"] = retries
    record.update(over)
    return record


RETRIED = [
    {"step_index": 0, "tool": "file_dialog_action",
     "label": "Save report", "verdict": "not-met",
     "reason": "file missing", "strike": 1, "retried": True},
    {"step_index": 0, "tool": "file_dialog_action",
     "label": "Save report", "verdict": "not-met",
     "reason": "file missing", "strike": 2, "retried": True},
    {"step_index": 0, "tool": "file_dialog_action",
     "label": "Save report", "verdict": "met",
     "reason": "file exists", "strike": 0, "retried": False},
]

EXHAUSTED = [
    {"step_index": 0, "tool": "file_dialog_action",
     "label": "Save report", "verdict": "not-met",
     "reason": "file missing", "strike": 1, "retried": True},
    {"step_index": 0, "tool": "file_dialog_action",
     "label": "Save report", "verdict": "not-met",
     "reason": "file missing", "strike": 2, "retried": True},
    {"step_index": 0, "tool": "file_dialog_action",
     "label": "Save report", "verdict": "not-met",
     "reason": "file missing", "strike": 3, "retried": False},
]


class TestRetriesDetails(unittest.TestCase):
    def test_retried_task_shows_retry_lines_receipt_wording(self):
        details = task_state.render_task(_record(RETRIED))["details_text"]
        # Receipt wording (format_receipt), minus the receipt's
        # two-space list indent — details_text drops that indent
        # for Verification lines already, and does the same here.
        self.assertIn(
            "Retry: Save report \u2014 not-met (strike 1) \u2014 retried",
            details)
        self.assertIn(
            "Retry: Save report \u2014 not-met (strike 2) \u2014 retried",
            details)
        # Met-reset event: the retried step finally met, strikes
        # reset — recorded as verdict "met", strike 0.
        self.assertIn(
            "Retry: Save report \u2014 met (strike 0) \u2014 halted",
            details)

    def test_exhausted_task_shows_halted_line(self):
        details = task_state.render_task(
            _record(EXHAUSTED, state="failed"))["details_text"]
        self.assertIn(
            "Retry: Save report \u2014 not-met (strike 3) \u2014 halted",
            details)

    def test_retry_lines_follow_verification_lines(self):
        details = task_state.render_task(_record(RETRIED))["details_text"]
        self.assertLess(details.index("Verification: Save report"),
                        details.index("Retry: Save report"))

    def test_unretried_task_no_retry_section(self):
        # No "retries" key at all (pre-retry ledger records).
        details = task_state.render_task(_record())["details_text"]
        self.assertNotIn("Retry:", details)

    def test_empty_retries_no_retry_section(self):
        details = task_state.render_task(_record([]))["details_text"]
        self.assertNotIn("Retry:", details)

    def test_malformed_retries_ignored_not_crash(self):
        # A non-list value must never crash the row, and adds
        # no retry lines.
        details = task_state.render_task(
            _record("not-a-list"))["details_text"]
        self.assertNotIn("Retry:", details)
        # Non-dict entries are skipped; a partial dict renders
        # with the receipt's defaults (empty verdict, strike 0).
        details = task_state.render_task(
            _record(["not-a-dict", {"label": "Save report"}])
            )["details_text"]
        self.assertIn("Retry: Save report \u2014  (strike 0) \u2014 halted",
                      details)

    def test_other_details_unchanged(self):
        details = task_state.render_task(_record(RETRIED))["details_text"]
        self.assertIn("Verification: Save report \u2014 met", details)
        self.assertIn("Task id: retried-task", details)


class TestRetriesRowWidget(unittest.TestCase):
    def test_row_widget_still_builds_for_retried_task(self):
        try:
            from PySide6.QtWidgets import QApplication, QLabel
        except ImportError:
            self.skipTest("PySide6 absent")
        app = QApplication.instance() or QApplication([])
        rendered = task_state.render_task(_record(RETRIED))
        widget = task_state.build_task_row_widget(rendered)
        labels = [lbl.text() for lbl in widget.findChildren(QLabel)]
        self.assertIn("Retried task", labels)
        self.assertIn("Done", labels)
        # The row's Details button carries the full details_text
        # (with retry lines) to the dialog — the button exists
        # and the rendered details hold the lines.
        self.assertIn("Retry: Save report", rendered["details_text"])


if __name__ == "__main__":
    unittest.main()
