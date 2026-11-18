"""Shared helper for the shipped tests: keep data/activity.log
byte-identical across a test run.

Several units under test (the executor, the confirmation gate)
append decision lines to data/activity.log — that is correct app
behaviour, but a test run on the user's own install must not dirty
the real data/ directory. ActivityLogGuard snapshots the file's
bytes on entry and restores them on exit (deleting the file only
if the run created it where none existed).
"""

from __future__ import annotations

import contextlib
import pathlib


class AuditLogGuard(contextlib.AbstractContextManager):
    """ActivityLogGuard's sibling for data/audit.jsonl:
    ToolRegistry.execute() appends one audit record per call
    (core/audit.py, v5.49 item 38) — correct app behaviour that
    a test run must not leave behind in the real data/ tree.
    (v5.50 R5-T: added when test_tools_execute_schema was found
    leaking exactly this file — it guarded activity.log only.)
    Same snapshot/restore semantics as ActivityLogGuard."""

    def __init__(self, package_root: pathlib.Path):
        self._path = pathlib.Path(package_root) / "data" / "audit.jsonl"
        self._existed = False
        self._bytes: bytes | None = None

    def __enter__(self):
        try:
            self._existed = self._path.exists()
            self._bytes = self._path.read_bytes() if self._existed else None
        except OSError:
            self._existed, self._bytes = False, None
        return self

    def __exit__(self, *exc):
        try:
            if self._existed:
                self._path.write_bytes(self._bytes or b"")
            elif self._path.exists():
                self._path.unlink()
        except OSError:
            pass
        return False


class ReceiptLogGuard(contextlib.AbstractContextManager):
    """ActivityLogGuard's sibling for data/tool_receipts.jsonl:

    Audit fix Batch 5 (TEST-01): the execution-schema suite's
    timeout leg records an UNKNOWN-outcome receipt (Phase 2
    C10: funnel "timeout" maps to "unknown"), and the
    unknown-outcome guard then refuses the identical leg on
    any repeat run until the state is verified. Guarding the
    receipt file with the same snapshot/restore semantics as
    activity/audit keeps a test run from leaving that receipt
    behind — but note the standing C10 rule the schema suite
    itself pins: hygiene must restore data to baseline
    BEFORE runs containing timeout tests, not just after,
    because the guard reads the live file (and the funnel's
    process-global in-memory store) at dispatch time. The
    guard alone is the after-half; the suite's setUpModule /
    setUp restore is the before-half.
    Same snapshot/restore semantics as AuditLogGuard."""

    def __init__(self, package_root: pathlib.Path):
        self._path = pathlib.Path(package_root) / "data" / "tool_receipts.jsonl"
        self._existed = False
        self._bytes: bytes | None = None

    def __enter__(self):
        try:
            self._existed = self._path.exists()
            self._bytes = self._path.read_bytes() if self._existed else None
        except OSError:
            self._existed, self._bytes = False, None
        return self

    def __exit__(self, *exc):
        try:
            if self._existed:
                self._path.write_bytes(self._bytes or b"")
            elif self._path.exists():
                self._path.unlink()
        except OSError:
            pass
        return False


class ActivityLogGuard(contextlib.AbstractContextManager):
    def __init__(self, package_root: pathlib.Path):
        self._path = pathlib.Path(package_root) / "data" / "activity.log"
        self._existed = False
        self._bytes: bytes | None = None

    def __enter__(self):
        try:
            self._existed = self._path.exists()
            self._bytes = self._path.read_bytes() if self._existed else None
        except OSError:
            self._existed, self._bytes = False, None
        return self

    def __exit__(self, *exc):
        try:
            if self._existed:
                self._path.write_bytes(self._bytes or b"")
            elif self._path.exists():
                # The run created it (an empty data/ log shell) —
                # restore absence only if it holds nothing but the
                # lines this run wrote; safest is to remove it.
                self._path.unlink()
        except OSError:
            pass
        return False
