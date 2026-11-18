"""UI Slice 3: single task-state component (complexity doc CX-05).

LAB HONESTY NOTE: gui/task_state.py imports only the standard
library at top level, so its pure mapping (render_task) executes
for real here — every state and every hard rule below is tested
against the real function, not a stub. The Qt row widget itself
(build_task_row_widget / add_task_row) cannot be driven in this
lab (real Qt widgets, frozen voice_assistant.* names in the
callers), so the widget wiring is verified by AST over the real
files. Rendered appearance on Windows is field proof, not
something this suite claims.
"""

import ast
import re
import unittest
from pathlib import Path

from gui.task_state import DISPLAY_STATES, render_task

TREE = Path(__file__).resolve().parent.parent


def _record(state, steps, outcomes, **extra):
    rec = {
        "task_id": "task-12345678",
        "classification": "saved_plan",
        "title": "Test task",
        "turn_text": "do the test thing",
        "state": state,
        "steps": steps,
        "outcomes": outcomes,
        "reason": "",
        "error_kind": "",
        "updated_at": "2026-10-30T10:00:00",
    }
    rec.update(extra)
    return rec


def _step(tool, label=None):
    return {"tool": tool, "args": {}, "label": label or tool,
            "on_fail": "halt", "when": None}


def _outcome(tool, status, label=None, kind="", message=""):
    return {"tool": tool, "label": label or tool, "status": status,
            "kind": kind, "message": message}


def _bare_add_item_arg(line):
    """Variable name of a bare ``.addItem(<name>)`` call, or
    None when the call carries a string (or anything else)."""
    match = re.search(r"\.addItem\(\s*(\w+)\s*\)\s*$", line)
    return match.group(1) if match else None


def _func_source(path, name):
    """Source segment of a function (top-level or method)."""
    text = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return "\n".join(lines[node.lineno - 1: node.end_lineno])
    raise AssertionError(f"{name} not found in {path}")


class TestStates(unittest.TestCase):
    def test_all_seven_states_render_distinctly(self):
        cases = [
            (_record("pending", [_step("a")], []), "Queued"),
            (_record("running", [_step("a")], []), "Running"),
            (_record("running", [_step("a"), _step("b")],
                     [_outcome("a", "success")],
                     ), "Running"),
            # Phase D (D9 producer-or-hide): the legacy marker is
            # ignored — the marker alone never displays Verifying.
            (_record("running", [_step("a")], [], verifying=True),
             "Running"),
            # Wiring item 3: the coordinator verification phase
            # writes the stored "verifying" state, so Verifying
            # displays — the phase is the real producer.
            (_record("verifying", [_step("a")],
                     [_outcome("a", "success")]), "Verifying"),
            (_record("running", [_step("approve_tool")], [],
                     ), "Running"),  # no pending hint: no approval claim
            (_record("completed", [_step("a")],
                     [_outcome("a", "success")]), "Done"),
            (_record("cancelled", [_step("a"), _step("b")],
                     [_outcome("a", "success")]), "Partial"),
            (_record("failed", [_step("a")],
                     [_outcome("a", "failure", kind="timeout",
                                message="timed out")]), "Failed"),
        ]
        seen = set()
        for rec, expected in cases:
            got = render_task(rec)["state"]
            self.assertEqual(got, expected,
                             f"{rec['state']} -> {got}, want {expected}")
            seen.add(got)
        for state in DISPLAY_STATES:
            if state != "Needs approval":
                self.assertIn(state, seen)
        # Needs approval needs the live pending hint (separate test).
        rec = _record("running", [_step("send_mail")], [])
        got = render_task(rec, pending_tool="send_mail",
                          pending_summary="Send mail to Aayur?")["state"]
        self.assertEqual(got, "Needs approval")
        seen.add(got)
        self.assertEqual(seen, set(DISPLAY_STATES))

    def test_done_requires_terminal_completed_state(self):
        # A successful handler return is NOT a completed goal.
        running_latest_success = _record(
            "running", [_step("a"), _step("b")],
            [_outcome("a", "success")])
        self.assertEqual(render_task(running_latest_success)["state"],
                         "Running")
        # Even all steps successful: still Running while the stored
        # state is running. Only state == "completed" renders Done.
        running_all_success = _record(
            "running", [_step("a"), _step("b")],
            [_outcome("a", "success"), _outcome("b", "success")])
        self.assertEqual(render_task(running_all_success)["state"],
                         "Running")
        self.assertEqual(render_task(running_all_success)["action"],
                         "Finishing up")
        completed = _record(
            "completed", [_step("a"), _step("b")],
            [_outcome("a", "success"), _outcome("b", "success")])
        self.assertEqual(render_task(completed)["state"], "Done")

    def test_done_with_skipped_steps_still_done_but_gaps_visible(self):
        rec = _record("completed", [_step("a"), _step("b")],
                      [_outcome("a", "success"),
                       _outcome("b", "skipped", kind="probe")])
        rendered = render_task(rec)
        self.assertEqual(rendered["state"], "Done")
        self.assertIn("skipped", rendered["rationale"])
        self.assertIn("⊘", rendered["details_text"])

    def test_partial_vs_failed_distinct(self):
        partial = render_task(_record(
            "cancelled", [_step("a"), _step("b")],
            [_outcome("a", "success")]))
        failed_empty = render_task(_record("cancelled", [_step("a")], []))
        failed = render_task(_record(
            "failed", [_step("a")],
            [_outcome("a", "failure", kind="timeout")]))
        self.assertEqual(partial["state"], "Partial")
        self.assertEqual(failed_empty["state"], "Failed")
        self.assertEqual(failed["state"], "Failed")
        self.assertNotEqual(partial["state"], failed["state"])
        self.assertNotEqual(partial["state"], "Done")
        self.assertNotEqual(failed["state"], "Done")

    def test_failed_shows_reason_and_failed_step(self):
        rec = _record("failed", [_step("a"), _step("b")],
                      [_outcome("a", "success"),
                       _outcome("b", "failure", kind="timeout",
                                message="tool timed out")])
        rendered = render_task(rec)
        self.assertIn("timeout", rendered["rationale"])
        self.assertIn("timed out", rendered["rationale"])
        self.assertIn("✗", rendered["details_text"])
        self.assertIn("tool timed out", rendered["details_text"])

    def test_crash_inconsistent_live_record_renders_failed(self):
        # The coordinator halts on a failure outcome, so a live
        # record carrying one is a crashed write: it cannot proceed.
        rec = _record("running", [_step("a"), _step("b")],
                      [_outcome("a", "success"),
                       _outcome("b", "failure", kind="handler_error")])
        self.assertEqual(render_task(rec)["state"], "Failed")

    def test_paused_is_running_with_qualifier(self):
        rec = _record("paused", [_step("a"), _step("b")],
                      [_outcome("a", "success")])
        rendered = render_task(rec)
        self.assertEqual(rendered["state"], "Running")
        self.assertTrue(rendered["paused"])
        self.assertTrue(rendered["action"].startswith("Paused"))

    def test_queued_and_unknown(self):
        queued = render_task(_record("pending", [_step("a"), _step("b")], []))
        self.assertEqual(queued["state"], "Queued")
        self.assertIn("2 step(s) queued", queued["rationale"])
        unknown = render_task(_record("bogus", [_step("a")], []))
        self.assertIsNone(unknown["state"])
        self.assertEqual(unknown["action"], "Task state unknown")
        self.assertIn("bogus", unknown["rationale"])

    def test_needs_approval_rules(self):
        rec = _record("running", [_step("read_file"), _step("send_mail")],
                      [_outcome("read_file", "success")])
        # Live pending request for the positioned tool: approval.
        got = render_task(rec, pending_tool="send_mail",
                          pending_summary="Send the mail?")
        self.assertEqual(got["state"], "Needs approval")
        self.assertIn("send_mail", got["action"])
        self.assertIn("Send the mail?", got["rationale"])
        # No pending request: the same record is just Running.
        self.assertEqual(render_task(rec)["state"], "Running")
        # Pending request for a DIFFERENT tool: still Running
        # (the dialog belongs to another execution, not this task).
        got = render_task(rec, pending_tool="delete_file")
        self.assertEqual(got["state"], "Running")
        # Pending request for an already-finished step: Running.
        got = render_task(rec, pending_tool="read_file")
        self.assertEqual(got["state"], "Running")

    def test_running_shows_action_and_progress(self):
        rec = _record("running",
                      [_step("open_app", "Open the browser"),
                       _step("send_mail", "Send the mail")],
                      [_outcome("open_app", "success")])
        rendered = render_task(rec)
        self.assertEqual(rendered["action"], "Send the mail")
        self.assertIn("step 2 of 2", rendered["rationale"])
        # Label == tool: no invented rationale, tool shown.
        rec2 = _record("running", [_step("send_mail")], [])
        rendered2 = render_task(rec2)
        self.assertEqual(rendered2["action"], "send_mail")

    def test_verifying_marker_ignored(self):
        # Wiring item 3 fulfils the D9 premise: the coordinator
        # verification phase IS the producer now, so Verifying
        # is a real display state — but the legacy marker alone
        # still changes nothing, and the record renders as
        # Running either way.
        rec = _record("running", [_step("a")],
                      [_outcome("a", "success")])
        self.assertEqual(render_task(rec)["state"], "Running")
        rec["verifying"] = True
        self.assertEqual(render_task(rec)["state"], "Running")
        self.assertIn("Verifying", DISPLAY_STATES)

    def test_title_fallback_and_details(self):
        rec = _record("completed", [_step("a")],
                      [_outcome("a", "success")])
        del rec["title"]
        rendered = render_task(rec)
        self.assertEqual(rendered["title"], "saved_plan")
        self.assertIn("Test task", render_task(_record(
            "completed", [_step("a")],
            [_outcome("a", "success")]))["details_text"])
        details = rendered["details_text"]
        self.assertIn("✓", details)
        self.assertIn("Progress: 1 done, 0 skipped, 0 failed",
                      details)
        rec2 = _record("failed", [_step("a")],
                       [_outcome("a", "failure")],
                       reason="step 'a' failure")
        self.assertIn("Reason: step 'a' failure",
                      render_task(rec2)["details_text"])


class TestWiring(unittest.TestCase):
    def test_component_module_is_stdlib_only_at_top(self):
        tree = ast.parse((TREE / "gui" / "task_state.py").read_text())
        top_imports = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                top_imports.add((node.module or "").split(".")[0])
        self.assertEqual(top_imports, {"__future__", "json"})

    def test_widget_imports_qt_lazily(self):
        tree = ast.parse((TREE / "gui" / "task_state.py").read_text())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) \
                    and node.name == "build_task_row_widget":
                src = ast.dump(node)
                self.assertIn("PySide6", src)
                self.assertIn("Details", src)
                self.assertIn("_show_details", src)
                return
        self.fail("build_task_row_widget not found")

    def test_activity_refresh_uses_single_component(self):
        # GUI split #12 batch 2 INTENTIONAL: _refresh_activity_tabs
        # moved verbatim to window_memory_activity.py. Assertion unchanged.
        src = _func_source(TREE / "gui" / "window_memory_activity.py",
                           "_refresh_activity_tabs")
        self.assertIn("render_task", src)
        self.assertIn("add_task_row", src)
        self.assertIn("pending_request", src)
        # The old per-tab state text is gone from this function.
        self.assertNotIn("r.get('state', '')", src)
        # Pending attribution is single-use (one dialog, one row).
        self.assertIn("pending_used", src)

    def test_no_other_task_row_renderer_in_activity(self):
        # addItem with a formatted task row must not survive
        # anywhere in the Activity refresh besides the empty /
        # unavailable placeholder strings.
        # UIS3 disposition 2026-11-03 INTENTIONAL: the scan judges
        # each addItem CALL, not each physical line. Batch 9's
        # journal fallback splits its call across two lines (the
        # placeholder string sits on the continuation line), so a
        # line-based scan false-positives on it — the call's
        # content already satisfied this rule. Joining a call's
        # lines by parenthesis balance changes nothing about the
        # rule itself: the placeholder allowlist below is
        # unchanged, and a wrapped formatted row still fails
        # because the joined call text contains no placeholder.
        # No assertion weakened; the unit of analysis is fixed.
        # GUI split #12 batch 2 INTENTIONAL: _refresh_activity_tabs
        # moved verbatim to window_memory_activity.py. Assertion unchanged.
        src = _func_source(TREE / "gui" / "window_memory_activity.py",
                           "_refresh_activity_tabs")
        logical = []
        buf = ""
        depth = 0
        for line in src.splitlines():
            buf = f"{buf} {line.strip()}" if buf else line
            depth += line.count("(") - line.count(")")
            if depth <= 0:
                logical.append(buf)
                buf = ""
        if buf:
            logical.append(buf)
        # Field-check fix #8 (U5 #32): a widget row is added as
        # an EMPTY item — QListWidgetItem() with no text — and
        # given its widget by setItemWidget; the reading lives
        # on the widget, so a formatted task row can never pass
        # through an empty item. Only those variables are
        # allowed as bare addItem arguments.
        empty_items = set(re.findall(
            r"(\w+)\s*=\s*QListWidgetItem\(\s*\)", src))
        for line in logical:
            if "addItem(" in line and "add_task_row" not in line:
                self.assertTrue(
                    "Nothing in progress" in line
                    or "No finished tasks yet" in line
                    or "unavailable right now" in line
                    # Restore 2026-11-01: the Mission Log's
                    # empty-filter placeholder — a placeholder,
                    # not a task row; real rows still go through
                    # add_task_row (see
                    # test_activity_refresh_uses_single_component).
                    or "No missions match this filter" in line
                    or _bare_add_item_arg(line) in empty_items,
                    f"unexpected row addItem: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
