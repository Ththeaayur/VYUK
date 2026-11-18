"""§7 item 8 — action journal + recovery (parent draft).

Lab honesty: every rule here is drivable headless with temp receipt
files and fake executors. Real crash/restart behaviour on Windows
stays field work and is not claimed here.

Temp receipt stores only (explicit paths) — nothing here touches the
tree's data/ or the process-wide default store.
"""

import json
import tempfile
import unittest
from pathlib import Path

from core.desktop import action_journal as aj  # INTENTIONAL (Phase 3 S7a-2): moved to core/desktop/
from core import tool_receipts as tr


class _Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "tool_receipts.jsonl"
        self.store = tr.ReceiptStore(path=self.path)

    def _fresh(self):
        """A relaunch: a brand-new store on the same file."""
        return tr.ReceiptStore(path=self.path)

    def _lines(self):
        return self.path.read_text(encoding="utf-8").splitlines()


# ----------------------------------------------------------------------
# Classification: crash between attempt and dispatch retries
# ----------------------------------------------------------------------

class TestCrashBetweenAttemptAndDispatch(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "tool_receipts.jsonl"
        self.store = tr.ReceiptStore(path=self.path)

    def test_never_dispatched_retries(self):
        attempt = self.store.record_attempt(
            tool="press_hotkey", args={"keys": ["ctrl", "s"]})
        entry = aj.entry_for(attempt)
        self.assertEqual(entry["state"], "attempted")
        self.assertEqual(aj.classify(entry), "retryable")
        # The guard agrees: a call that never ran guards nothing.
        self.assertIsNone(self.store.find_unknown(
            tool="press_hotkey", args={"keys": ["ctrl", "s"]}))

    def test_relaunch_rereads_journal_not_memory(self):
        attempt = self.store.record_attempt(
            tool="press_hotkey", args={"keys": ["ctrl", "s"]})
        self.store.mark_dispatched(attempt["receipt_id"])
        # Simulated crash: no outcome recorded. A fresh process
        # must see the same unknown from the FILE alone.
        relaunched = tr.ReceiptStore(path=self.path)
        entries = aj.load_entries(relaunched)
        self.assertEqual(len(entries), 1)
        self.assertEqual(aj.classify(entries[0]), "unknown")
        plan = aj.plan_resume(relaunched)
        self.assertEqual(plan["counts"]["unknown"], 1)
        self.assertEqual(plan["counts"]["retryable"], 0)


# ----------------------------------------------------------------------
# Outcome-known never re-runs; cancelled splits by dispatch
# ----------------------------------------------------------------------

class TestOutcomeKnown(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "tool_receipts.jsonl"
        self.store = tr.ReceiptStore(path=self.path)

    def _attempt_with_outcome(self, status, dispatched=True):
        attempt = self.store.record_attempt(
            tool="file_control", args={"path": "a.txt"})
        if dispatched:
            self.store.mark_dispatched(attempt["receipt_id"])
        self.store.record_outcome(
            attempt["receipt_id"], status=status,
            summary="done", dispatched=dispatched)
        return attempt["receipt_id"]

    def test_succeeded_failed_refused_are_done(self):
        for status in ("succeeded", "failed", "refused"):
            rid = self._attempt_with_outcome(status)
            entry = aj.entry_for(
                [a for a in self.store.attempts()
                 if a["receipt_id"] == rid][0])
            self.assertEqual(entry["state"], "outcome")
            self.assertEqual(entry["outcome"], status)
            self.assertEqual(aj.classify(entry), "done")
        plan = aj.plan_resume(self.store)
        self.assertEqual(plan["counts"]["done"], 3)
        self.assertEqual(plan["counts"]["retryable"], 0)
        self.assertEqual(plan["counts"]["unknown"], 0)

    def test_cancelled_before_dispatch_is_done_not_retryable(self):
        rid = self._attempt_with_outcome("cancelled", dispatched=False)
        entry = aj.entry_for(
            [a for a in self.store.attempts()
             if a["receipt_id"] == rid][0])
        # Outcome known AND user-cancelled: recovery never re-runs
        # an action the user cancelled, even though it never ran.
        self.assertEqual(aj.classify(entry), "done")
        self.assertIsNone(self.store.find_unknown(
            tool="file_control", args={"path": "a.txt"}))

    def test_cancelled_during_dispatch_is_unknown(self):
        rid = self._attempt_with_outcome("cancelled", dispatched=True)
        entry = aj.entry_for(
            [a for a in self.store.attempts()
             if a["receipt_id"] == rid][0])
        self.assertEqual(entry["state"], "dispatched")
        self.assertIsNone(entry["outcome"])
        self.assertEqual(aj.classify(entry), "unknown")
        self.assertIsNotNone(self.store.find_unknown(
            tool="file_control", args={"path": "a.txt"}))


# ----------------------------------------------------------------------
# Unknown stays blocked until reconciled
# ----------------------------------------------------------------------

class TestReconcile(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "tool_receipts.jsonl"
        self.store = tr.ReceiptStore(path=self.path)

    def _unknown_attempt(self):
        attempt = self.store.record_attempt(
            tool="send_email", args={"to": "a@b.c"})
        self.store.mark_dispatched(attempt["receipt_id"])
        return attempt["receipt_id"]

    def test_unknown_stays_blocked(self):
        rid = self._unknown_attempt()
        plan = aj.plan_resume(self.store)
        self.assertEqual(plan["counts"]["unknown"], 1)
        self.assertIsNotNone(self.store.find_unknown(
            tool="send_email", args={"to": "a@b.c"}))
        entry = aj.entry_for(
            [a for a in self.store.attempts()
             if a["receipt_id"] == rid][0])
        self.assertEqual(entry["state"], "dispatched")

    def test_reconcile_requires_verification_and_confirmer(self):
        rid = self._unknown_attempt()
        with self.assertRaises(ValueError):
            aj.reconcile(self.store, receipt_id=rid, happened=False,
                         verification="", confirmed_by="user")
        with self.assertRaises(ValueError):
            aj.reconcile(self.store, receipt_id=rid, happened=False,
                         verification="checked the outbox",
                         confirmed_by="  ")
        # Both refusals left the entry unknown and blocked.
        entry = aj.entry_for(
            [a for a in self.store.attempts()
             if a["receipt_id"] == rid][0])
        self.assertEqual(aj.classify(entry), "unknown")

    def test_reconcile_verified_absent_releases_guard(self):
        rid = self._unknown_attempt()
        before = self._lines()
        result = aj.reconcile(
            self.store, receipt_id=rid, happened=False,
            verification="outbox empty, recipient got nothing",
            confirmed_by="user")
        self.assertEqual(result["new_outcome"], "failed")
        entry = aj.entry_for(
            [a for a in self.store.attempts()
             if a["receipt_id"] == rid][0])
        self.assertEqual(aj.classify(entry), "done")
        self.assertIsNone(self.store.find_unknown(
            tool="send_email", args={"to": "a@b.c"}))
        # Append-only: the original attempt line is byte-identical.
        after = self._lines()
        self.assertEqual(after[0], before[0])
        self.assertGreater(len(after), len(before))

    def test_reconcile_verified_happened_marks_succeeded(self):
        rid = self._unknown_attempt()
        result = aj.reconcile(
            self.store, receipt_id=rid, happened=True,
            verification="recipient confirmed receipt",
            confirmed_by="user")
        self.assertEqual(result["new_outcome"], "succeeded")
        entry = aj.entry_for(
            [a for a in self.store.attempts()
             if a["receipt_id"] == rid][0])
        self.assertEqual(aj.classify(entry), "done")
        self.assertIsNone(self.store.find_unknown(
            tool="send_email", args={"to": "a@b.c"}))

    def test_reconcile_refuses_non_unknown(self):
        # Never-dispatched: retryable, not reconcilable.
        attempt = self.store.record_attempt(
            tool="press_hotkey", args={"keys": ["ctrl", "s"]})
        with self.assertRaises(ValueError):
            aj.reconcile(self.store, receipt_id=attempt["receipt_id"],
                         happened=True, verification="x",
                         confirmed_by="user")
        # Outcome-known: nothing to reconcile.
        self.store.mark_dispatched(attempt["receipt_id"])
        self.store.record_outcome(
            attempt["receipt_id"], status="succeeded", summary="ok")
        with self.assertRaises(ValueError):
            aj.reconcile(self.store, receipt_id=attempt["receipt_id"],
                         happened=True, verification="x",
                         confirmed_by="user")
        # Unknown id.
        with self.assertRaises(ValueError):
            aj.reconcile(self.store, receipt_id="no-such-id",
                         happened=False, verification="x",
                         confirmed_by="user")

    def _lines(self):
        return self.path.read_text(encoding="utf-8").splitlines()


# ----------------------------------------------------------------------
# Compensations: reverse, verified, never guessed
# ----------------------------------------------------------------------

class TestCompensations(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "tool_receipts.jsonl"
        self.store = tr.ReceiptStore(path=self.path)
        self.comps = [
            {"tool": "step_one", "args": {"n": 1}},
            {"tool": "step_two", "args": {"n": 2}},
            {"tool": "step_three", "args": {"n": 3}},
        ]
        attempt = self.store.record_attempt(
            tool="batch_rename", args={"dir": "d"},
            compensations=self.comps,
            manual_steps=["rename the files back by hand"])
        self.entry = aj.entry_for(attempt)

    def test_plan_reverses_declaration_order(self):
        plan = aj.compensation_plan(self.entry)
        self.assertEqual(plan["status"], "declared")
        self.assertEqual([s["tool"] for s in plan["steps"]],
                         ["step_three", "step_two", "step_one"])

    def test_run_all_verified_in_reverse(self):
        ran = []

        def executor(step):
            ran.append(step["tool"])
            return {"status": "succeeded"}

        result = aj.run_compensations(
            self.store, self.entry, executor=executor,
            confirmed_by="user")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(ran, ["step_three", "step_two", "step_one"])
        self.assertEqual(len(result["verified"]), 3)

    def test_failure_stops_run(self):
        ran = []

        def executor(step):
            ran.append(step["tool"])
            if step["tool"] == "step_two":
                return {"status": "failed"}
            return {"status": "succeeded"}

        result = aj.run_compensations(
            self.store, self.entry, executor=executor,
            confirmed_by="user")
        self.assertEqual(result["status"], "stopped")
        # Reversed: step_three ran, step_two failed, step_one never ran.
        self.assertEqual(ran, ["step_three", "step_two"])
        self.assertEqual(result["stopped_at"]["tool"], "step_two")

    def test_executor_error_stops_run(self):
        def executor(step):
            raise RuntimeError("boom")

        result = aj.run_compensations(
            self.store, self.entry, executor=executor,
            confirmed_by="user")
        self.assertEqual(result["status"], "stopped")
        self.assertIn("boom", result["reason"])

    def test_no_declared_compensation_is_no_undo(self):
        attempt = self.store.record_attempt(
            tool="send_email", args={"to": "a@b.c"})
        entry = aj.entry_for(attempt)
        called = []

        def executor(step):
            called.append(step)
            return {"status": "succeeded"}

        result = aj.run_compensations(
            self.store, entry, executor=executor, confirmed_by="user")
        self.assertEqual(result["status"], "no_undo_available")
        self.assertEqual(called, [])
        self.assertTrue(result["manual_steps"])

    def test_compensation_requires_confirmation(self):
        called = []

        def executor(step):
            called.append(step)
            return {"status": "succeeded"}

        with self.assertRaises(ValueError):
            aj.run_compensations(self.store, self.entry,
                                 executor=executor, confirmed_by="")
        self.assertEqual(called, [])

    def test_bad_descriptor_refused_at_record_time(self):
        with self.assertRaises(ValueError):
            self.store.record_attempt(
                tool="x", args={}, compensations=[{"tool": "y"}])
        with self.assertRaises(ValueError):
            self.store.record_attempt(
                tool="x", args={}, compensations=[{"tool": "y",
                                                   "args": "nope"}])
        with self.assertRaises(ValueError):
            self.store.record_attempt(
                tool="x", args={},
                compensations=[{"tool": "y", "args": {}}] * 33)


# ----------------------------------------------------------------------
# Corrupt journal fails loudly; journal never stores credentials
# ----------------------------------------------------------------------

class TestCorruptAndCredentials(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "tool_receipts.jsonl"
        self.store = tr.ReceiptStore(path=self.path)

    def test_corrupt_line_fails_loudly_with_repair_path(self):
        self.store.record_attempt(tool="press_hotkey",
                                  args={"keys": ["ctrl", "s"]})
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write("{this is not json\n")
        fresh = tr.ReceiptStore(path=self.path)
        with self.assertRaises(ValueError) as ctx:
            fresh.attempts()
        self.assertIn(str(self.path), str(ctx.exception))
        self.assertIn(":2:", str(ctx.exception))
        check = aj.check_journal(self.store)
        self.assertFalse(check["ok"])
        self.assertTrue(check["repair_steps"])
        self.assertTrue(any("Back up" in s
                            for s in check["repair_steps"]))

    def test_check_journal_ok_on_good_store(self):
        self.store.record_attempt(tool="press_hotkey",
                                  args={"keys": ["ctrl", "s"]})
        check = aj.check_journal(self.store)
        self.assertTrue(check["ok"])
        self.assertEqual(check["entries"], 1)

    def test_journal_never_stores_credentials(self):
        attempt = self.store.record_attempt(
            tool="send_email",
            args={"to": "a@b.c", "password": "s3cret-value-123",
                  "api_key": "k3y-value-456"},
            handle_id="th-window-3",
            snapshot_stamp="deadbeef-stamp")
        entry = aj.entry_for(attempt)
        blob = json.dumps(entry, default=str)
        self.assertNotIn("s3cret-value-123", blob)
        self.assertNotIn("k3y-value-456", blob)
        # ...but the signature (a hash) and the evidence fields exist.
        self.assertTrue(entry["args_signature"])
        self.assertEqual(entry["handle_id"], "th-window-3")
        self.assertEqual(entry["snapshot_stamp"], "deadbeef-stamp")

    def test_handle_and_stamp_defaults_are_present(self):
        attempt = self.store.record_attempt(
            tool="press_hotkey", args={"keys": ["ctrl", "s"]})
        entry = aj.entry_for(attempt)
        self.assertIn("handle_id", entry)
        self.assertIn("snapshot_stamp", entry)
        self.assertIsNone(entry["handle_id"])
        self.assertIsNone(entry["snapshot_stamp"])
        self.assertEqual(entry["compensations"], [])


# ----------------------------------------------------------------------
# Journeys: resume after interruption, read-out
# ----------------------------------------------------------------------

class TestJourneys(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "tool_receipts.jsonl"
        self.store = tr.ReceiptStore(path=self.path)

    def test_kill_mid_task_resume_without_duplicate_effect(self):
        # Two actions: one crashed DURING dispatch (unknown), one
        # crashed BEFORE dispatch (never ran).
        during = self.store.record_attempt(
            tool="send_email", args={"to": "a@b.c"})
        self.store.mark_dispatched(during["receipt_id"])
        before = self.store.record_attempt(
            tool="press_hotkey", args={"keys": ["ctrl", "s"]})
        # Relaunch: resume from the journal file alone.
        relaunched = tr.ReceiptStore(path=self.path)
        plan = aj.plan_resume(relaunched)
        self.assertEqual(plan["counts"]["unknown"], 1)
        self.assertEqual(plan["counts"]["retryable"], 1)
        # User confirms the dispatched effect DID happen: done,
        # never re-executed by recovery (no duplicate effect).
        aj.reconcile(relaunched, receipt_id=during["receipt_id"],
                     happened=True,
                     verification="recipient confirmed receipt",
                     confirmed_by="user")
        # The never-dispatched action retries as a NEW attempt
        # through the funnel — the old entry is left untouched.
        retry = relaunched.record_attempt(
            tool="press_hotkey", args={"keys": ["ctrl", "s"]})
        self.assertNotEqual(retry["receipt_id"],
                            before["receipt_id"])
        old = [a for a in relaunched.attempts()
               if a["receipt_id"] == before["receipt_id"]][0]
        self.assertEqual(old["status"], "attempted")
        self.assertFalse(old["dispatched"])

    def test_read_out_newest_first_with_counts(self):
        first = self.store.record_attempt(
            tool="press_hotkey", args={"keys": ["ctrl", "s"]})
        self.store.record_attempt(
            tool="file_control", args={"path": "a.txt"})
        out = aj.read_out(self.store, limit=1)
        self.assertEqual(len(out["entries"]), 1)
        self.assertNotIn(first["receipt_id"][:8], out["entries"][0])
        self.assertEqual(out["counts"]["retryable"], 2)
