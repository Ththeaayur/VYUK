"""Round-3 section-F fix queue item 1 (F-F202 + F-F203 + F-F501):
outcome honesty — no receipt row may claim success for an act
that did not happen.

The bug (section F, parent-confirmed from the live code): approved
calls whose act did NOT happen were recorded ``succeeded`` in the
receipts. ``receipt_outcome_for_result`` fell through to
``"succeeded"`` unless the string passed ``is_failure_text``, which
matches only ``failed``-as-verb or the single browser-session
opener — so a JSON-string verdict ``'{"ok": false, "reason": ...}'``
read ``succeeded`` while the identical dict form read ``failed``,
and plain-string honest failures (``"Path not found: ..."``,
``"Folder not found: ..."``, ``"Google isn't connected yet ..."``)
all read ``succeeded``.

The fix, two seams (the detector is narrow by design — the fix
belongs at the verdict, not by widening the sniffing):

1. Receipt reader (``core/tool_receipts.py``): a JSON-string payload
   that parses as a FAILURE verdict (``ok`` False, or ``status``
   ``needs-observation``) is decoded and judged by the dict branch
   — the same verdict, one envelope out. A successful JSON-string
   verdict keeps its raw-string summary, and JSON data with no
   verdict keys is never treated as a verdict.
2. Handlers (the verdict seam): the exact plain-string failure
   branches return ``_outcome.failed(message, cause)`` instead of
   a plain string. The funnel unwraps an Outcome to its plain
   message string, so the brain-visible text and the funnel status
   (``"success"``) are unchanged; the verdict rides in
   ``outcome_verified`` / ``outcome_kind``, and the receipt reader
   already maps ``verified is False and outcome_kind == "failed"``
   to ``("failed", summary)``.

Covered here: the JSON-string classifier shapes, the dict shapes
(regression), the detector-not-widened rule, and every filed
handler shape through the real funnel (``tools.execute`` -> the
temp receipts store -> the outcome row read back).

Environment notes: the ``paste_text`` leg needs ``pyperclip``
unimportable (true in both lab interpreters); the
``connect_wifi`` leg needs a non-Windows host; the
``create_calendar_event`` leg needs no Google token in data/
(true for pristine data); the ``adapter_action`` leg needs the
foreground NOT to identify ``chrome`` (true in the lab — no
Chrome window exists there).

This suite dirties data/ (audit records + activity lines, like
every suite that drives execute()) — restore data/ pristine
afterwards.
"""

import json
import pathlib
import tempfile
import unittest

from core import confirm_gate, injection, tool_receipts
from core.funnel import rule_of_two as _rot
from core.funnel.tools_registry import tools


def _fresh_state():
    """Clear the session taint + Rule-of-Two legs between calls."""
    injection.clear_taint(force=True)
    _rot.reset_for_tests()


class _ReceiptBase(unittest.TestCase):
    """Temp receipts store + a confirm handler, per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="r3ff01_")
        self._receipts = (pathlib.Path(self._tmp.name)
                          / "tool_receipts.jsonl")
        tool_receipts.install_store(self._receipts)
        tool_receipts.reset_receipt_degraded()
        self.asked = []
        _fresh_state()

    def tearDown(self):
        confirm_gate.clear_handler()
        tool_receipts.reset_default()
        tool_receipts.reset_receipt_degraded()
        self._tmp.cleanup()
        _fresh_state()

    def _register(self, approve=True):
        def _handler(name, summary=None, *a, **k):
            self.asked.append((name, summary))
            return approve

        confirm_gate.register_handler(_handler)
        try:
            import voice_assistant.core.confirm_gate as _alias
            _alias.register_handler(_handler)
        except Exception:  # noqa: BLE001 — alias is best-effort
            pass

    def _outcome_row(self, tool):
        rows = []
        if not self._receipts.exists():
            return None
        for ln in self._receipts.read_text(
                encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:  # noqa: BLE001 — skip a bad line
                continue
        for row in reversed(rows):
            if row.get("type") != "outcome":
                continue
            rid = row.get("receipt_id")
            for a in rows:
                if (a.get("type") == "attempt"
                        and a.get("receipt_id") == rid
                        and a.get("tool") == tool):
                    return row
        return None


def _ok(payload):
    return {"status": "success", "kind": "", "message": "",
            "result": payload}


class TestJsonStringVerdicts(_ReceiptBase):
    """The receipt reader decodes a JSON-string FAILURE verdict."""

    def test_json_string_ok_false_with_reason_is_failed(self):
        status, summary = tool_receipts.receipt_outcome_for_result(
            _ok('{"ok": false, "reason": "no such file"}'))
        self.assertEqual(status, "failed")
        self.assertEqual(summary, "no such file")

    def test_json_string_ok_false_empty_is_failed(self):
        status, summary = tool_receipts.receipt_outcome_for_result(
            _ok('{"ok": false}'))
        self.assertEqual(status, "failed")
        self.assertEqual(summary, "")

    def test_json_string_needs_observation_is_failed(self):
        status, summary = tool_receipts.receipt_outcome_for_result(
            _ok('{"status": "needs-observation", '
                '"reason": "still running"}'))
        self.assertEqual(status, "failed")
        self.assertEqual(summary, "still running")

    def test_json_string_ok_true_keeps_raw_summary(self):
        # A successful JSON-string verdict is NOT re-routed: the
        # fix only decodes failure verdicts, so the raw string
        # stays the summary (unchanged behaviour).
        raw = '{"ok": true, "report": "renamed 1 file"}'
        status, summary = tool_receipts.receipt_outcome_for_result(
            _ok(raw))
        self.assertEqual(status, "succeeded")
        self.assertEqual(summary, raw)

    def test_json_string_data_without_verdict_keys_untouched(self):
        # JSON data is not a verdict: no "ok"/"status" keys, so the
        # string is read as plain text, never judged.
        raw = '{"label": "Larger text", "enabled": true}'
        status, summary = tool_receipts.receipt_outcome_for_result(
            _ok(raw))
        self.assertEqual(status, "succeeded")
        self.assertEqual(summary, raw)

    def test_json_string_malformed_stays_plain_string(self):
        for raw in ('{"ok": false, ', '{"ok": false', '{not json'):
            with self.subTest(raw=raw):
                status, summary = (
                    tool_receipts.receipt_outcome_for_result(_ok(raw)))
                self.assertEqual(status, "succeeded", raw)
                self.assertEqual(summary, raw)

    def test_json_string_with_leading_whitespace_decodes(self):
        status, summary = tool_receipts.receipt_outcome_for_result(
            _ok('  \n{"ok": false, "reason": "no such file"}  '))
        self.assertEqual(status, "failed")
        self.assertEqual(summary, "no such file")

    def test_dict_verdicts_still_green(self):
        cases = [
            ({"ok": False, "reason": "cdp-click-failed: no such element"},
             ("failed", "cdp-click-failed: no such element")),
            ({"ok": False, "report": "click did not land"},
             ("failed", "click did not land")),
            ({"ok": True, "report": "renamed 1 file"},
             ("succeeded", "renamed 1 file")),
            ({"ok": False}, ("failed", "")),
        ]
        for payload, want in cases:
            with self.subTest(payload=payload):
                self.assertEqual(
                    tool_receipts.receipt_outcome_for_result(_ok(payload)),
                    want)

    def test_plain_string_path_not_found_stays_succeeded(self):
        # The detector is NOT widened: a plain "Path not found"
        # string still reads "succeeded" at the classifier — the
        # fix for plain strings is at the handler (the verdict
        # seam), never in the sniffing.
        status, summary = tool_receipts.receipt_outcome_for_result(
            _ok("Path not found: /x"))
        self.assertEqual(status, "succeeded")
        self.assertEqual(summary, "Path not found: /x")


class TestFunnelOutcomeRows(_ReceiptBase):
    """Every filed handler shape through the real funnel."""

    def _assert_failed_row(self, tool, out, fragment):
        self.assertEqual(out.get("status"), "success", out)
        self.assertFalse(out.get("outcome_verified"), out)
        self.assertEqual(out.get("outcome_kind"), "failed", out)
        row = self._outcome_row(tool)
        self.assertIsNotNone(row, f"no outcome row recorded for {tool}")
        self.assertEqual(row["status"], "failed", row)
        self.assertIn(fragment, row["summary"], row)
        return row

    def test_f202_json_string_verdict_is_failed(self):
        self._register(approve=True)
        out = tools.execute("access_record_preference", {
            "preference_json": json.dumps(
                {"label": "Larger text", "enabled": True, "score": 9}),
            "output_path": str(pathlib.Path(self._tmp.name)
                               / "pref_bad.json")})
        # The handler returns the JSON-string verdict; the receipt
        # reader decodes it, so the row is failed with the reason
        # named — and no file was written.
        self.assertEqual(out.get("status"), "success", out)
        row = self._outcome_row("access_record_preference")
        self.assertIsNotNone(row, "no outcome row recorded")
        self.assertEqual(row["status"], "failed", row)
        self.assertIn("unexpected preference field 'score'",
                      row["summary"], row)
        self.assertFalse((pathlib.Path(self._tmp.name)
                          / "pref_bad.json").exists())

    def test_f203a_file_control_delete_missing_is_failed(self):
        self._register(approve=True)
        missing = str(pathlib.Path(self._tmp.name) / "no_such.txt")
        out = tools.execute("file_control", {
            "action": "delete", "path": missing, "confirm": True})
        row = self._assert_failed_row("file_control", out,
                                      "Path not found")
        self.assertIn(missing, row["summary"], row)

    def test_f203b_paste_text_unavailable_is_failed(self):
        self._register(approve=True)
        out = tools.execute("paste_text", {"text": "r3ff01 probe"})
        self._assert_failed_row("paste_text", out, "isn't available")

    def test_f501_batch_rename_missing_folder_is_failed(self):
        self._register(approve=True)
        out = tools.execute("batch_rename", {
            "folder": str(pathlib.Path(self._tmp.name) / "no_such_dir"),
            "find": "x", "replace": "y", "confirm": True})
        self._assert_failed_row("batch_rename", out, "Folder not found")

    def test_f501_delete_snippet_missing_is_failed(self):
        self._register(approve=True)
        out = tools.execute("delete_snippet",
                            {"name": "r3ff01-no-such-snippet"})
        self._assert_failed_row("delete_snippet", out, "no snippet")

    def test_f501_delete_routine_missing_is_failed(self):
        self._register(approve=True)
        out = tools.execute("delete_routine",
                            {"name": "r3ff01-no-such-routine"})
        self._assert_failed_row("delete_routine", out, "No routine named")

    def test_f501_adapter_action_mismatch_is_failed(self):
        self._register(approve=True)
        out = tools.execute("adapter_action", {
            "phrase": "open the file menu", "app_id": "chrome"})
        self._assert_failed_row("adapter_action", out,
                                "does not identify")

    def test_f501_connect_wifi_linux_is_failed(self):
        self._register(approve=True)
        out = tools.execute("connect_wifi",
                            {"ssid": "R3FF01TestNet", "confirm": True})
        self._assert_failed_row("connect_wifi", out, "netsh")

    def test_f501_create_calendar_event_unconnected_is_failed(self):
        self._register(approve=True)
        out = tools.execute("create_calendar_event", {
            "title": "R3FF01 probe", "start": "2026-11-22T10:00:00",
            "end": "2026-11-22T11:00:00"})
        self._assert_failed_row("create_calendar_event", out,
                                "isn't connected yet")

    def test_copy_move_rename_missing_source_is_failed(self):
        # Same-function identical hole as the delete shape: a
        # copy/move/rename whose source does not exist.
        self._register(approve=True)
        missing = str(pathlib.Path(self._tmp.name) / "no_such.txt")
        for action in ("copy", "move", "rename"):
            with self.subTest(action=action):
                out = tools.execute("file_control", {
                    "action": action, "path": missing,
                    "destination": str(pathlib.Path(self._tmp.name)
                                       / "dest.txt")})
                self._assert_failed_row("file_control", out,
                                        "Path not found")

    def test_funnel_status_and_text_unchanged_for_failed_act(self):
        # The brain-visible text and the funnel status are
        # unchanged: the verdict rides in outcome_verified /
        # outcome_kind, not in the status or the sentence.
        self._register(approve=True)
        missing = str(pathlib.Path(self._tmp.name) / "no_such.txt")
        out = tools.execute("file_control", {
            "action": "delete", "path": missing, "confirm": True})
        self.assertEqual(out.get("status"), "success", out)
        self.assertEqual(out.get("result"), f"Path not found: {missing}",
                         out)

    def test_successful_batch_rename_stays_succeeded(self):
        self._register(approve=True)
        folder = pathlib.Path(self._tmp.name) / "rename"
        folder.mkdir()
        (folder / "old.txt").write_text("r3ff01\n")
        out = tools.execute("batch_rename", {
            "folder": str(folder), "find": "old", "replace": "new",
            "confirm": True})
        self.assertEqual(out.get("status"), "success", out)
        row = self._outcome_row("batch_rename")
        self.assertIsNotNone(row, "no outcome row recorded")
        self.assertEqual(row["status"], "succeeded", row)
        self.assertIn("Renamed 1 file(s)", row["summary"], row)
        self.assertTrue((folder / "new.txt").exists())

    def test_valid_preference_stays_succeeded(self):
        self._register(approve=True)
        out = tools.execute("access_record_preference", {
            "preference_json": json.dumps(
                {"label": "Larger text", "enabled": True}),
            "output_path": str(pathlib.Path(self._tmp.name)
                               / "pref.json")})
        self.assertEqual(out.get("status"), "success", out)
        row = self._outcome_row("access_record_preference")
        self.assertIsNotNone(row, "no outcome row recorded")
        self.assertEqual(row["status"], "succeeded", row)
        self.assertTrue((pathlib.Path(self._tmp.name)
                         / "pref.json").exists())

    def test_declined_batch_rename_is_refused(self):
        self._register(approve=False)
        folder = pathlib.Path(self._tmp.name) / "rename"
        folder.mkdir()
        (folder / "keep.txt").write_text("r3ff01\n")
        out = tools.execute("batch_rename", {
            "folder": str(folder), "find": "keep", "replace": "kept",
            "confirm": True})
        self.assertEqual(out.get("status"), "refused", out)
        row = self._outcome_row("batch_rename")
        self.assertIsNotNone(row, "no outcome row recorded")
        self.assertEqual(row["status"], "refused", row)
        self.assertTrue((folder / "keep.txt").exists())


if __name__ == "__main__":
    unittest.main()
