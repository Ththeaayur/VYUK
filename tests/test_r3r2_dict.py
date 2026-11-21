"""Round-3 section C fix queue item 12 (R2): dict-result outcome.

The re-verification found what the item-7 repro could not: the
receipt classifier only inspected STRING results. A dict result
with ``ok: False`` (a killed-mid-click ``cdp-click-failed``, a
``needs-observation`` pick-first, a route-death pause) recorded
``("succeeded", "")`` — the exact forbidden shape from Finding 2,
unchanged. And a non-success result whose ``message`` is empty
recorded ``("failed", "")`` — status right, cause missing.

Two corrections to the re-verification's reading, both proven
from the raw evidence while building this fix (see the report):
(1) the coordinator's probe read ``outcome_summary`` off OUTCOME
rows, which carry ``summary`` — so its ("failed", "") readings
for browser_click / browser_read were a probe artifact: the live
C2-5/C2-6 verdict JSONs show the failed rows carrying the full
cause in ``summary``. A browser_click on a dead Chrome already
recorded ("failed", <cause>) before this fix; the repro keeps it
as a control. (2) The receipt writer DOES see the final
normalised dict the brain sees — the _audited closure runs after
normalization and the failure script, and TOOLS.execute returns
that same dict. The hole was entirely in the classifier, which
never looked inside a dict payload; no writer plumbing changed.

The fix (core.tool_receipts.receipt_outcome_for_result only):
a dict payload's own verdict is read — ``ok`` False (or a
``needs-observation`` status) reads "failed" with the reason
(or report) named; a successful dict reads "succeeded" with
its report as the summary (a successful READ still keeps an
empty summary, so no content leaks); and the non-success branch
falls back to the result text (or the dict's reason) when the
message is empty. An explicit Outcome.failed verdict still wins
over every heuristic; an unverified act stays an honest success
(B13); the "may not" no-effect click still records "succeeded".

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

CDP_CLICK_REASON = ("Browser action failed: couldn't click 'Press me' "
                    "(Locator.scroll_into_view_if_needed: Target page, "
                    "context or browser has been closed).")
NEEDS_OBS_REASON = ("the sight route is live — pick from a fresh "
                    "observation first (pick_browser / pick_desktop), "
                    "then click the picked point visibly")


def _fresh_state():
    """Clear the session taint + Rule-of-Two legs between calls."""
    injection.clear_taint(force=True)
    _rot.reset_for_tests()


class _ReceiptBase(unittest.TestCase):
    """Temp receipts store + a confirm handler, per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="r3r2_")
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


class TestDictOutcomeForResult(_ReceiptBase):
    """The classifier reads a dict payload's own verdict."""

    def test_ok_false_cdp_click_failed(self):
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": {"ok": False, "reason": CDP_CLICK_REASON,
                       "rule": "cdp-click-failed"}})
        self.assertEqual(status, "failed")
        self.assertEqual(summary, CDP_CLICK_REASON)

    def test_ok_false_report_only(self):
        # No 'reason' key: the report names the cause.
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": {"ok": False,
                       "report": "the click did not land"}})
        self.assertEqual(status, "failed")
        self.assertEqual(summary, "the click did not land")

    def test_needs_observation_is_failed(self):
        # A pick-first return clicked nothing, so it must NOT
        # read as success (B2) — and the receipt must say why.
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": {"ok": False, "status": "needs-observation",
                       "reason": NEEDS_OBS_REASON,
                       "action": "click", "target": "Press me",
                       "by": "dom", "route": "sight",
                       "report": NEEDS_OBS_REASON}})
        self.assertEqual(status, "failed")
        self.assertEqual(summary, NEEDS_OBS_REASON)

    def test_needs_observation_status_alone_is_failed(self):
        # Defensive: the status names a non-landed act even
        # without an ok flag.
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": {"status": "needs-observation",
                       "reason": NEEDS_OBS_REASON}})
        self.assertEqual(status, "failed")
        self.assertEqual(summary, NEEDS_OBS_REASON)

    def test_paused_dict_is_failed_with_reason(self):
        # A route-death pause: the act definitively did not
        # happen, so the attempt resolves as failed (the guard
        # then allows a later re-run) with the pause reason
        # visible in the summary.
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": {"ok": False, "status": "paused",
                       "reason": "the cdp route died — the goal pauses",
                       "rule": "M-29-route-death"}})
        self.assertEqual(status, "failed")
        self.assertIn("the cdp route died", summary)

    def test_ok_true_dict_success_carries_report(self):
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": {"ok": True, "action": "click",
                       "target": "Press me", "route": "cdp",
                       "status": "clicked",
                       "report": "Clicked 'Press me'. The page text changed."}})
        self.assertEqual(status, "succeeded")
        self.assertEqual(summary, "Clicked 'Press me'. The page text changed.")

    def test_ok_true_dict_read_keeps_empty_summary(self):
        status, summary = tool_receipts.receipt_outcome_for_result(
            {"status": "success",
             "result": {"ok": True, "report": "3 rows returned"}},
            is_read=True)
        self.assertEqual(status, "succeeded")
        self.assertEqual(summary, "")

    def test_data_dict_without_ok_stays_succeeded(self):
        # A plain data dict is a success payload, not a verdict.
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": {"count": 3, "items": ["a", "b"]}})
        self.assertEqual(status, "succeeded")
        self.assertEqual(summary, "")

    def test_ok_false_without_reason_or_report(self):
        # The cause genuinely isn't named anywhere: failed with
        # an empty summary is the honest record then.
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": {"ok": False}})
        self.assertEqual(status, "failed")
        self.assertEqual(summary, "")

    def test_explicit_failed_verdict_wins_over_dict(self):
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": {"ok": False, "reason": CDP_CLICK_REASON,
                       "rule": "cdp-click-failed"},
            "outcome_verified": False,
            "outcome_evidence": "no re-read available",
            "outcome_kind": "failed"})
        self.assertEqual(status, "failed")
        self.assertIn("couldn't click", summary)

    def test_explicit_unverified_dict_stays_success(self):
        # B13: an unverified act is an honest success, never a
        # failure — even when the payload carries ok False, the
        # explicit verdict already said exactly what happened.
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": {"ok": False, "reason": CDP_CLICK_REASON,
                       "rule": "cdp-click-failed"},
            "outcome_verified": False,
            "outcome_evidence": "no re-read available",
            "outcome_kind": "unverified"})
        self.assertEqual(status, "succeeded")
        self.assertIn("couldn't click", summary)


class TestNonSuccessFallback(_ReceiptBase):
    """A non-success result falls back to the result text."""

    def test_failure_string_without_message(self):
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "failure", "kind": "handler_error",
            "result": ("Browser action failed: couldn't search for "
                       "'Press me' (Locator.count: Target page, "
                       "context or browser has been closed).")})
        self.assertEqual(status, "failed")
        self.assertIn("couldn't search", summary)

    def test_message_wins_over_result_text(self):
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "failure", "kind": "handler_error",
            "message": "the human line",
            "result": "the raw text"})
        self.assertEqual(status, "failed")
        self.assertEqual(summary, "the human line")

    def test_error_kind_without_message(self):
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "error", "kind": "handler_error",
            "result": "the raw text"})
        self.assertEqual(status, "failed")
        self.assertEqual(summary, "the raw text")

    def test_failure_dict_without_message(self):
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "failure", "kind": "handler_error",
            "result": {"ok": False, "reason": CDP_CLICK_REASON,
                       "rule": "cdp-click-failed"}})
        self.assertEqual(status, "failed")
        self.assertIn("couldn't click", summary)

    def test_refused_without_message_falls_back(self):
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "refused", "kind": "refused",
            "result": "the gate said no"})
        self.assertEqual(status, "refused")
        self.assertEqual(summary, "the gate said no")

    def test_timeout_without_message(self):
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "timeout", "kind": "timeout",
            "result": "the target stopped responding"})
        self.assertEqual(status, "unknown")
        self.assertEqual(summary, "the target stopped responding")


class TestFunnelDictOutcomeRows(_ReceiptBase):
    """Real funnel execute() calls with dict payloads, no Chrome."""

    def test_click_anywhere_mark_needs_observation_is_failed(self):
        # A grounding-mark click with no fresh observation: on
        # the CDP route the handler returns the mark _fail, on
        # the sight route the needs-observation dict, with no
        # route live a pause dict — every shape is an ok:false
        # dict, and the outcome row must read "failed" with the
        # reason, never "succeeded" with an empty summary.
        self._register(approve=True)
        out = tools.execute("click_anywhere", {
            "action": "click", "target": "Press me", "by": "mark",
            "name": "Press me", "role": "button"})
        self.assertEqual(out.get("status"), "success", out)
        nested = out.get("result")
        self.assertIsInstance(nested, dict, out)
        self.assertIs(nested.get("ok"), False, out)
        row = self._outcome_row("click_anywhere")
        self.assertIsNotNone(row, "no outcome row recorded")
        self.assertEqual(row["status"], "failed", row)
        self.assertTrue(row["summary"], row)
        self.assertIn("observation", row["summary"], row)

    def test_no_effect_click_still_succeeded(self):
        # Item-7 pin: a click with no observed effect ("may not")
        # is not a failure — the classifier must not promote it.
        status, summary = tool_receipts.receipt_outcome_for_result({
            "status": "success",
            "result": ("Clicked 'X'. No effect followed — the page "
                       "did not change, so it may not have done "
                       "anything.")})
        self.assertEqual(status, "succeeded")
        self.assertIn("Clicked 'X'", summary)

    def test_dict_read_success_keeps_empty_summary_funnel(self):
        # A successful READ whose payload is a dict keeps an empty
        # summary at the classifier level (no content leak).
        status, summary = tool_receipts.receipt_outcome_for_result(
            {"status": "success",
             "result": {"ok": True, "report": "page text here"}},
            is_read=True)
        self.assertEqual(status, "succeeded")
        self.assertEqual(summary, "")


if __name__ == "__main__":
    unittest.main()
