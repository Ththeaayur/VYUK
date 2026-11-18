"""Doctor receipt-store health check (regression-batch leftover).

The doctor gained a read-only receipt-store check: it reports the
store at data/tool_receipts.jsonl as PASS (healthy, with counts),
WARN (missing — expected on a fresh install, possible history loss
on an established one), or FAIL (unreadable, corrupt, or malformed
— the guard cannot use the store, and the store itself refuses
writes while corrupt).

Stdlib unittest only. The check is exercised against temp-dir
stores only — never the live data/ store — so this suite never
dirties real data. The check never prints receipt contents: one
test plants a distinctive args_preview string and asserts it
never appears in the output. Read-only proof includes an
over-prune-bound store (ReceiptStore would rewrite it on load;
the check must leave its bytes untouched).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import unittest

_DOCTOR_PATH = pathlib.Path(__file__).resolve().parent.parent / "doctor.py"


def _load_doctor():
    spec = importlib.util.spec_from_file_location("doctor_under_test", _DOCTOR_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _attempt(rid, **extra):
    rec = {"type": "attempt", "receipt_id": rid, "tool": "type_text",
           "args_signature": "aa" * 32, "created_at": 1700000000.0}
    rec.update(extra)
    return rec


def _outcome(rid, status, **extra):
    rec = {"type": "outcome", "receipt_id": rid, "status": status,
           "created_at": 1700000060.0}
    rec.update(extra)
    return rec


def _dispatched(rid):
    return {"type": "dispatched", "receipt_id": rid}


class TestDoctorReceiptStore(unittest.TestCase):
    def setUp(self):
        self.doctor = _load_doctor()
        self._tmp = pathlib.Path(
            __import__("tempfile").mkdtemp(prefix="receipt_store_test_"))
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self._tmp, ignore_errors=True))

    def _store(self, name="tool_receipts.jsonl"):
        return self._tmp / name

    def _write_lines(self, path, lines):
        path.write_text("\n".join(lines) + ("\n" if lines else ""),
                        encoding="utf-8")
        return path

    def _run_check(self, path):
        """Run the check with stdout captured; return (counts, output)."""
        self.doctor._counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.doctor.check_receipt_store(path)
        return dict(self.doctor._counts), buf.getvalue()

    def test_healthy_store_passes_with_counts(self):
        # r1: dispatched + succeeded outcome -> resolved.
        # r2: attempt with no dispatched field -> reads as
        # dispatched (the safe side) and no outcome -> guards.
        lines = [
            json.dumps(_attempt("r1", dispatched=False)),
            json.dumps(_dispatched("r1")),
            json.dumps(_outcome("r1", "succeeded")),
            json.dumps(_attempt("r2")),
        ]
        path = self._write_lines(self._store(), lines)
        counts, out = self._run_check(path)
        self.assertEqual(counts, {"PASS": 1, "WARN": 0, "FAIL": 0})
        self.assertIn("[PASS] Tool receipt store", out)
        self.assertIn("2 attempts, 1 outcomes, 1 dispatch marks", out)
        self.assertIn("1 attempt(s) still without a resolved outcome", out)

    def test_missing_store_warns(self):
        counts, out = self._run_check(self._store())
        self.assertEqual(counts, {"PASS": 0, "WARN": 1, "FAIL": 0})
        self.assertIn("[WARN] Tool receipt store", out)
        self.assertIn("no store at", out)

    def test_empty_and_blank_only_stores_pass(self):
        path = self._write_lines(self._store(), [])
        counts, out = self._run_check(path)
        self.assertEqual(counts, {"PASS": 1, "WARN": 0, "FAIL": 0})
        self.assertIn("is empty (no receipts yet)", out)
        path = self._write_lines(self._store(), ["", "   "])
        counts, out = self._run_check(path)
        self.assertEqual(counts, {"PASS": 1, "WARN": 0, "FAIL": 0})
        self.assertIn("is empty (no receipts yet; 2 blank line(s) ignored)", out)

    def test_corrupt_store_fails_with_line_number(self):
        lines = [
            json.dumps(_attempt("r1")),
            "{this is not json",
            json.dumps(_attempt("r2")),
        ]
        path = self._write_lines(self._store(), lines)
        counts, out = self._run_check(path)
        self.assertEqual(counts, {"PASS": 0, "WARN": 0, "FAIL": 1})
        self.assertIn("[FAIL] Tool receipt store", out)
        self.assertIn("1 corrupt line(s) (first: line 2:", out)

    def test_malformed_entries_counted_by_kind(self):
        lines = [
            json.dumps([1, 2, 3]),  # non-object
            json.dumps({"type": "bogus", "receipt_id": "x"}),  # unknown type
            json.dumps({"receipt_id": "y"}),  # no type -> unknown type
            json.dumps(_attempt("r1")),
        ]
        path = self._write_lines(self._store(), lines)
        counts, out = self._run_check(path)
        self.assertEqual(counts, {"PASS": 0, "WARN": 0, "FAIL": 1})
        self.assertIn("3 malformed record(s)", out)
        self.assertIn("non-object x1", out)
        self.assertIn("unknown-type x2", out)

    def test_unreadable_store_fails(self):
        # A directory in place of the file: deterministic on any
        # user, unlike chmod 0 (root still reads).
        counts, out = self._run_check(self._tmp)
        self.assertEqual(counts, {"PASS": 0, "WARN": 0, "FAIL": 1})
        self.assertIn("could not be read", out)

    def test_unreadable_permission_fails_or_skips(self):
        path = self._write_lines(self._store(),
                                 [json.dumps(_attempt("r1"))])
        os.chmod(path, 0)
        try:
            if os.access(path, os.R_OK):
                raise unittest.SkipTest(
                    "file still readable after chmod 0 (running as root?)")
            counts, out = self._run_check(path)
            self.assertEqual(counts, {"PASS": 0, "WARN": 0, "FAIL": 1})
            self.assertIn("could not be read", out)
        finally:
            os.chmod(path, 0o644)

    def test_unknown_join_rules(self):
        lines = [
            # Undispatched attempt: never ran, does not guard.
            json.dumps(_attempt("a", dispatched=False)),
            # Dispatched then cancelled-before-dispatch outcome:
            # outcome dispatched=False releases the guard.
            json.dumps(_attempt("b", dispatched=False)),
            json.dumps(_dispatched("b")),
            json.dumps(_outcome("b", "cancelled", dispatched=False)),
            # Dispatched then cancelled-during-dispatch: guards.
            json.dumps(_attempt("c", dispatched=False)),
            json.dumps(_dispatched("c")),
            json.dumps(_outcome("c", "cancelled", dispatched=True)),
            # Dispatched, timed out (unknown): guards.
            json.dumps(_attempt("d", dispatched=False)),
            json.dumps(_dispatched("d")),
            json.dumps(_outcome("d", "unknown")),
            # Resolved: does not guard.
            json.dumps(_attempt("e", dispatched=False)),
            json.dumps(_dispatched("e")),
            json.dumps(_outcome("e", "failed")),
        ]
        path = self._write_lines(self._store(), lines)
        scan = self.doctor.scan_receipt_store(path)
        self.assertEqual(scan["unknown_attempts"], 2)  # c and d only
        self.assertEqual(scan["outcome_status"]["cancelled"], 2)
        self.assertEqual(scan["outcome_status"]["unknown"], 1)
        self.assertEqual(scan["outcome_status"]["failed"], 1)

    def test_never_prints_receipt_contents(self):
        marker = "SECRET_PREVIEW_MARKER_DO_NOT_PRINT_7f3a"
        lines = [
            json.dumps(_attempt("r1", args_preview=marker,
                                tool="send_email")),
            json.dumps(_attempt("r2", args_preview="some other text")),
        ]
        path = self._write_lines(self._store(), lines)
        counts, out = self._run_check(path)
        self.assertEqual(counts["PASS"], 1)
        self.assertNotIn(marker, out)
        self.assertNotIn("send_email", out)
        self.assertNotIn("some other text", out)

    def test_read_only_including_over_prune_bound(self):
        # 5005 lines: ReceiptStore would prune this to 5000 on
        # load (a rewrite). The check must leave bytes + mtime
        # untouched.
        lines = [json.dumps(_attempt(f"r{i}")) for i in range(5005)]
        path = self._write_lines(self._store(), lines)
        before_bytes = path.read_bytes()
        before_mtime = path.stat().st_mtime_ns
        counts, out = self._run_check(path)
        self.assertEqual(counts["PASS"], 1)
        self.assertIn("over the 5000-line prune bound", out)
        self.assertEqual(path.read_bytes(), before_bytes)
        self.assertEqual(path.stat().st_mtime_ns, before_mtime)

    def test_check_registered_in_checks(self):
        names = [c.__name__ for c in self.doctor._CHECKS]
        self.assertIn("check_receipt_store", names)


if __name__ == "__main__":
    unittest.main()
