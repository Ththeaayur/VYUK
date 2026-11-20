"""Round-3 section C fix queue item 7 (C2-F2): receipt outcome
records dispatch success, not act success; summary always empty.

The bug (section C, parent-confirmed from the raw C2-6 verdict
JSON): the live leg's failed ``browser_click`` ("Browser action
failed: couldn't search for 'Press me' ...") has an outcome row
``status: "succeeded"``, ``summary: ""`` in tool_receipts.jsonl.
The funnel's normalized result for a failed act is still
``status: "success"`` — a plain-string failure passes through
as a success payload, and an explicit outcome.failed verdict
rides as ``outcome_verified=False`` + ``outcome_kind="failed"``
on a success dict — so the receipt writer, which mapped only
the funnel status, called every dispatched act a success and
never populated the summary (the success dict has no "message"
key).

The fix: core.tool_receipts.receipt_outcome_for_result reads the
FULL result — the structured verdict first (failed -> "failed",
unverified stays an honest success, B13), then a plain-string
failure sentence (core.funnel.tool_result.is_failure_text) ->
"failed", else "succeeded" — and the summary is the result's
own message, or the act's report string for a successful
effectful call. A successful READ keeps an empty summary: its
result is data, not a description, and copying it into the
receipts file would leak content there.

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
from core.funnel import tool_result as _tr
from core.funnel.tools_registry import tools

C26_SENTENCE = ("Browser action failed: couldn't search for "
                "'Press me' (Locator.count: Target page, context "
                "or browser has been closed).")


def _fresh_state():
    """Clear the session taint + Rule-of-Two legs between calls."""
    injection.clear_taint(force=True)
    _rot.reset_for_tests()


class _ReceiptBase(unittest.TestCase):
    """Temp receipts store + a confirm handler, per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="r3c2f2_")
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


class TestReceiptOutcomeForResult(_ReceiptBase):
    """The pure derivation: the act's verdict, not the dispatch."""

    def test_c26_sentence_is_failed(self):
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success", "result": C26_SENTENCE})
        self.assertEqual(status, "failed")
        self.assertEqual(summary, C26_SENTENCE)

    def test_no_session_opener_is_failed(self):
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": ("No controllable browser session is active. "
                       "Tap 'Connect my browser' and I'll set it up.")})
        self.assertEqual(status, "failed")
        self.assertIn("No controllable browser session", summary)

    def test_failure_sentence_family(self):
        for text in (
                "Download failed: [Errno 28] No space left on device",
                "File operation 'delete' failed: [Errno 2] No such file",
                "The transcription failed inside the model runtime (x).",
                "Working out that subnet failed: bad mask",
                "Snapshot failed for 'daily' (disk full).",
                "ONVIF discovery failed (timeout).",
                "Recording failed for 'mic': device busy.",
                "Text reading couldn't start: the OCR library failed "
                "to load (missing).",
                "File operation 'copy' failed: permission denied",
                "Network action 'get' failed: connection refused",
                "Zip operation 'extract' failed: bad archive",
                "OCR failed: tesseract not found",
                "Restore failed: no backup found",
                "Webcam capture failed: no device",
                "Window control failed: no such window",
                "Media control failed: no player",
                "Virtual desktop control failed: unsupported",
                "Web search failed: no network",
                "Weather fetch failed: timeout",
                "Converting that timestamp failed: bad format",
                "Comparing those versions failed: unparsable",
                "Querying that CSV failed: no such column",
                "Face detection failed on 'photo.jpg': no face",
                "Reading the end of that file failed: truncated",
        ):
            with self.subTest(text=text[:40]):
                status, _ = tool_receipts.receipt_outcome_for_result(
                    {"status": "success", "result": text})
                self.assertEqual(status, "failed", text)

    def test_success_prose_stays_succeeded(self):
        for text in (
                "Opened 'Field Check C2 Test Page'",
                "'C2-MARKER-4417' appears 1 time(s) on the page",
                "File written: /tmp/x.txt (5 chars)",
                "3 files copied",
                "The download failed earlier, but it worked on retry",
                "1 failed and was retried",
        ):
            with self.subTest(text=text[:40]):
                status, summary = (
                    tool_receipts.receipt_outcome_for_result(
                        {"status": "success", "result": text}))
                self.assertEqual(status, "succeeded", text)
                self.assertEqual(summary, text)

    def test_structured_failed_verdict(self):
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": "Could not write file: [Errno 30] Read-only",
            "outcome_verified": False,
            "outcome_evidence": "[Errno 30] Read-only",
            "outcome_kind": "failed"})
        self.assertEqual(status, "failed")
        self.assertIn("Could not write file", summary)

    def test_structured_unverified_stays_success(self):
        # B13: an unverified act is an honest success, never a
        # failure — the world was not re-read.
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": "Clicked 'Save' — could not confirm it landed",
            "outcome_verified": False,
            "outcome_evidence": "no re-read available",
            "outcome_kind": "unverified"})
        self.assertEqual(status, "succeeded")
        self.assertIn("Clicked 'Save'", summary)

    def test_structured_verified_stays_success(self):
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": "File written: /tmp/x.txt (5 chars)",
            "outcome_verified": True,
            "outcome_evidence": "5 bytes on disk",
            "outcome_kind": "verified"})
        self.assertEqual(status, "succeeded")
        self.assertIn("File written:", summary)

    def test_non_success_statuses_unchanged(self):
        cases = [
            ({"status": "failure", "kind": "handler_error",
              "message": "boom"}, "failed", "boom"),
            ({"status": "refused", "kind": "refused",
              "message": "no"}, "refused", "no"),
            ({"status": "timeout", "kind": "timeout",
              "message": "slow"}, "unknown", "slow"),
            ({"status": "refused", "kind": "timeout",
              "message": "slow"}, "unknown", "slow"),
            ({"status": "error", "kind": "handler_error",
              "message": "boom"}, "failed", "boom"),
            ({"status": "error", "kind": "validation",
              "message": "bad"}, "refused", "bad"),
            ({"status": "cancelled", "kind": "cancelled",
              "message": "stop"}, "unknown", "stop"),
        ]
        for result, want_status, want_summary in cases:
            with self.subTest(result=result):
                status, summary = (
                    tool_receipts.receipt_outcome_for_result(result))
                self.assertEqual(status, want_status)
                self.assertEqual(summary, want_summary)

    def test_non_dict_is_unknown(self):
        self.assertEqual(
            tool_receipts.receipt_outcome_for_result("plain"),
            ("unknown", ""))
        self.assertEqual(
            tool_receipts.receipt_outcome_for_result(None),
            ("unknown", ""))

    def test_read_success_keeps_empty_summary(self):
        status, summary = tool_receipts.receipt_outcome_for_result(
            {"status": "success", "result": "secret file content"},
            is_read=True)
        self.assertEqual(status, "succeeded")
        self.assertEqual(summary, "")

    def test_read_failure_still_names_cause(self):
        status, summary = tool_receipts.receipt_outcome_for_result(
            {"status": "success", "result": C26_SENTENCE},
            is_read=True)
        self.assertEqual(status, "failed")
        self.assertEqual(summary, C26_SENTENCE)

    def test_is_failure_text_helpers(self):
        self.assertTrue(_tr.is_failure_text(C26_SENTENCE))
        self.assertTrue(_tr.is_failure_text(
            "No controllable browser session is active. Tap X."))
        self.assertFalse(_tr.is_failure_text(
            "Opened 'Field Check C2 Test Page'"))
        self.assertFalse(_tr.is_failure_text(None))
        self.assertFalse(_tr.is_failure_text(42))


class TestFunnelOutcomeRows(_ReceiptBase):
    """Real funnel execute() calls: the row must match the act."""

    def test_structured_failed_write_is_failed(self):
        self._register(approve=True)
        out = tools.execute("write_file", {
            "filepath": "/sys/c2f2_probe.txt", "content": "c2f2"})
        self.assertEqual(out.get("status"), "success", out)
        self.assertFalse(out.get("outcome_verified"), out)
        self.assertEqual(out.get("outcome_kind"), "failed", out)
        row = self._outcome_row("write_file")
        self.assertIsNotNone(row, "no outcome row recorded")
        self.assertEqual(row["status"], "failed", row)
        self.assertIn("Could not write file", row["summary"], row)

    def test_plain_failed_click_is_failed(self):
        # No Chrome attached: the handler returns the plain
        # no-session string. browser_click is NOT confirm-gated
        # in this tree, so no handler is needed — but registering
        # one is harmless.
        self._register(approve=True)
        out = tools.execute("browser_click", {
            "by": "role_button", "target": "Press me"})
        self.assertEqual(out.get("status"), "success", out)
        row = self._outcome_row("browser_click")
        self.assertIsNotNone(row, "no outcome row recorded")
        self.assertEqual(row["status"], "failed", row)
        self.assertIn("No controllable browser session",
                      row["summary"], row)

    def test_successful_write_is_succeeded_with_summary(self):
        self._register(approve=True)
        path = str(pathlib.Path(self._tmp.name) / "ok.txt")
        out = tools.execute("write_file", {
            "filepath": path, "content": "c2f2-ok"})
        self.assertEqual(out.get("status"), "success", out)
        row = self._outcome_row("write_file")
        self.assertIsNotNone(row, "no outcome row recorded")
        self.assertEqual(row["status"], "succeeded", row)
        self.assertTrue(row["summary"].startswith("File written:"),
                        row)

    def test_successful_read_keeps_empty_summary(self):
        path = pathlib.Path(self._tmp.name) / "read.txt"
        path.write_text("c2f2-secret-content", encoding="utf-8")
        out = tools.execute("read_file", {"filepath": str(path)})
        self.assertEqual(out.get("status"), "success", out)
        row = self._outcome_row("read_file")
        self.assertIsNotNone(row, "no outcome row recorded")
        self.assertEqual(row["status"], "succeeded", row)
        self.assertEqual(row["summary"], "", row)
        self.assertNotIn("c2f2-secret-content",
                         json.dumps(row), row)

    def test_declined_write_is_refused(self):
        self._register(approve=False)
        path = str(pathlib.Path(self._tmp.name) / "declined.txt")
        out = tools.execute("write_file", {
            "filepath": path, "content": "x"})
        self.assertEqual(out.get("status"), "refused", out)
        row = self._outcome_row("write_file")
        self.assertIsNotNone(row, "no outcome row recorded")
        self.assertEqual(row["status"], "refused", row)
        self.assertNotEqual(row["status"], "succeeded", row)


if __name__ == "__main__":
    unittest.main()
