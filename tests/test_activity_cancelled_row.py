"""Activity cancelled-row proof: the cancelled row renders Partial /
Failed in the real row widget — never "Needs approval".

What this suite pins BEYOND test_review_cancel_wiring (which
already pins the pure mapping for a cancelled record carrying a
stale pending dict):
* the LIVE-HINT vector: even if a live confirm-gate hint were
  passed to render_task for a cancelled record, state still
  decides first — Partial / Failed, never Needs approval.
  (In _refresh_activity_tabs the History loop passes no hint
  at all, so this is defense-in-depth, pinned, not assumed.)
* the DETAILS-TEXT vector: a cancelled record with a stale
  pending dict carries no "Pending approval:" line in its
  details text (coord_pending is only read for "paused").
* the WIDGET layer: build_task_row_widget puts the mapped
  state in the row's bold label — the render PNG is not just
  a dict assertion but the label a user sees.
* the PLACEMENT: _refresh_activity_tabs sorts "cancelled"
  into History (done), never In progress (live), via source
  scan — window_memory.py is unimportable in the lab without
  PortAudio (standing MainWindow lab limit), so the filter is
  pinned by scan, not by driving the page.

The visual proof is ~/workspace/render_activity_cancelled.py,
which saves ~/workspace/code-lab/activity_cancelled_row_render.png:
cancelled-with-success -> Partial, cancelled-without-success
(and a stale pending dict) -> Failed, paused+pending ->
Needs approval, completed -> Done, all in one list.

Pure tests run on both interpreters. Qt tests need PySide6
(the .venv-qt interpreter); on an interpreter without it they
skip, following the programme's standing optional-dependency
skips.
"""

import ast
import os
import pathlib
import re
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gui import task_state  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication, QLabel, QListWidget
    _QT = True
except ImportError:
    _QT = False

PENDING = {
    "step_index": 0,
    "tool": "menu_action",
    "label": "Close tab",
    "sentence": "Menu path refused (C-14 approve-first): "
                "'close tab' needs approval",
    "confirm_arg": "confirmed",
}


def _record(state, pending=None, outcomes=(), title="T"):
    record = {
        "task_id": "t1",
        "title": title,
        "state": state,
        "steps": [{"tool": "menu_action", "label": "Close tab",
                   "args": {"action": "list"}}],
        "outcomes": list(outcomes),
    }
    if pending is not None:
        record["pending_approval"] = dict(pending)
    return record


SUCCESS = [{"tool": "menu_action", "label": "Close tab",
            "status": "success"}]


def _refresh_source():
    """_refresh_activity_tabs source, whitespace-normalized."""
    # GUI split #12 batch 2 INTENTIONAL: _refresh_activity_tabs
    # moved verbatim to window_memory_activity.py. Assertion unchanged.
    text = (ROOT / "gui" / "window_memory_activity.py").read_text()
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if (isinstance(node, ast.FunctionDef)
                and node.name == "_refresh_activity_tabs"):
            segment = "\n".join(
                text.splitlines()[node.lineno - 1:node.end_lineno])
            return re.sub(r"\s+", " ", segment)
    raise AssertionError("_refresh_activity_tabs not found")


class TestCancelledRowMapping(unittest.TestCase):
    def test_cancelled_beats_live_hint(self):
        # The History loop passes no hint; even if one were
        # passed, the cancelled branch decides before the
        # running-branch hint logic.
        for outcomes, expected in ((SUCCESS, "Partial"), ((), "Failed")):
            rendered = task_state.render_task(
                _record("cancelled", pending=PENDING,
                        outcomes=outcomes),
                pending_tool="menu_action",
                pending_summary="a dialog is open")
            self.assertEqual(rendered["state"], expected)

    def test_cancelled_details_carry_no_pending_line(self):
        rendered = task_state.render_task(
            _record("cancelled", pending=PENDING, outcomes=SUCCESS))
        self.assertEqual(rendered["state"], "Partial")
        self.assertNotIn("Pending approval:", rendered["details_text"])
        # Contrast: the same pending dict on a paused record
        # DOES surface (that is the Needs approval row).
        paused = task_state.render_task(
            _record("paused", pending=PENDING))
        self.assertEqual(paused["state"], "Needs approval")
        self.assertIn("Pending approval:", paused["details_text"])

    def test_history_placement_by_source_scan(self):
        src = _refresh_source()
        # In progress = running/paused only; cancelled is not there.
        self.assertIn('in ("running", "paused")', src)
        # History = completed/failed/cancelled.
        self.assertIn('in ("completed", "failed", "cancelled")', src)
        # History rows render with NO live-gate hint passed.
        self.assertIn(
            "add_task_row(history, render_task(r), parent=history)", src)


@unittest.skipUnless(_QT, "PySide6 required for widget assertions")
class TestCancelledRowWidget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _widget_labels(self, rendered):
        widget = task_state.build_task_row_widget(rendered)
        return [lbl.text()
                for lbl in widget.findChildren(QLabel)]

    def test_widget_state_labels(self):
        cases = [
            (_record("cancelled", pending=PENDING, outcomes=SUCCESS),
             "Partial"),
            (_record("cancelled", pending=PENDING), "Failed"),
            (_record("paused", pending=PENDING), "Needs approval"),
            (_record("completed", outcomes=SUCCESS), "Done"),
        ]
        for record, expected in cases:
            labels = self._widget_labels(task_state.render_task(record))
            self.assertIn(expected, labels)
            if record["state"] == "cancelled":
                self.assertNotIn("Needs approval", labels)

    def test_list_rows_match_render(self):
        # The render script's harness as a test: four rows in
        # one list, each widget carrying its mapped state label.
        rows = [
            (_record("cancelled", outcomes=SUCCESS,
                     title="Cancelled — one step done"), "Partial"),
            (_record("cancelled", pending=PENDING,
                     title="Cancelled — nothing done"), "Failed"),
            (_record("paused", pending=PENDING,
                     title="Paused awaiting approval"), "Needs approval"),
            (_record("completed", outcomes=SUCCESS,
                     title="Finished task"), "Done"),
        ]
        lst = QListWidget()
        for record, _expected in rows:
            task_state.add_task_row(
                lst, task_state.render_task(record), parent=lst)
        self.assertEqual(lst.count(), len(rows))
        for i, (_rec, expected) in enumerate(rows):
            widget = lst.itemWidget(lst.item(i))
            labels = [lbl.text()
                      for lbl in widget.findChildren(QLabel)]
            self.assertIn(expected, labels)


if __name__ == "__main__":
    unittest.main()
