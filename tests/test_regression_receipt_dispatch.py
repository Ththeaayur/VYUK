"""Regression batch R2 — receipt dispatched-state guard.

The C10 unknown-outcome guard (5.98) recorded an attempt before dispatch
but never marked dispatch start, so a crash between the attempt record
and dispatch left an unknown attempt for a call that NEVER ran — and
the guard then refused every identical retry with no user exit. The
fix: attempts start dispatched=False; the funnel marks dispatched
immediately before a handler/bus dispatch (native choke point after
the last gate, MCP bus call site); find_unknown skips unmarked
attempts. The during-dispatch unknown case STAYS guarded by design.

Also pinned: the _unknown pre-filter is a COUNT, not a set — one
same-signature attempt resolving must not unguard another
still-unknown attempt sharing the signature (proven by the probe
sequence: plain call + unmarked seed + same-signature confirm
refusal + mark seed + retry must still refuse).

Funnel tests use the REAL tools.execute funnel with the REAL
registry (testleg pattern): whole-data-tree guard, taint cleared,
confirm handler cleared, provenance user-started with the lock off.
The tool is file_control (confirm-gated): a guard-passed call ends
in not_confirmed (nothing ran — no side effect), a guard-blocked
call ends in refused:unknown-prior. Args paths are unique per test
so no test's receipts can collide with another's.

Stdlib unittest only.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from core import confirm_gate, injection
    from core import tool_receipts as receipts_mod
    from core.funnel import tools_gate as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
    from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
    from core.config import config as app_config
except Exception as exc:  # noqa: BLE001 — missing dep: skip, not error
    raise unittest.SkipTest(f"core modules unavailable: {exc}")

try:
    from core.platform import reset_backend_cache
except Exception:  # noqa: BLE001 — optional seam
    reset_backend_cache = None

_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DATA_DIR = _PACKAGE_ROOT / "data"


class _DataGuard:
    """Snapshot/restore the ENTIRE working data/ tree per test."""

    def __init__(self):
        self._saved = {}

    def _scan(self):
        found = {}
        for path in sorted(_DATA_DIR.rglob("*")):
            if path.is_file():
                try:
                    found[path.relative_to(_DATA_DIR)] = path.read_bytes()
                except OSError:
                    pass
        return found

    def __enter__(self):
        self._saved = self._scan()
        return self

    def __exit__(self, *exc):
        current = self._scan()
        for rel in current:
            if rel not in self._saved:
                try:
                    (_DATA_DIR / rel).unlink()
                except OSError:
                    pass
        for rel, data in self._saved.items():
            path = _DATA_DIR / rel
            try:
                if not path.exists() or path.read_bytes() != data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            except OSError:
                pass
        return False


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = pathlib.Path(tempfile.mkdtemp(prefix="regression_dispatch_"))
        self._guard = _DataGuard()
        self._guard.__enter__()
        try:
            self._orig_env = os.environ.pop("VYUK_PLATFORM", None)
            if reset_backend_cache is not None:
                try:
                    reset_backend_cache()
                except Exception:
                    pass
            injection.clear_taint(force=True)
            confirm_gate.clear_handler()
            self._orig_prov = tools_mod.session_provenance()
            tools_mod.set_session_provenance("user")
            self._orig_lock = getattr(app_config, "session_provenance_lock", False)
            app_config.session_provenance_lock = False
        except Exception:
            self._guard.__exit__(None, None, None)
            shutil.rmtree(self._tmp, ignore_errors=True)
            raise

    def tearDown(self):
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        if self._orig_env is None:
            os.environ.pop("VYUK_PLATFORM", None)
        else:
            os.environ["VYUK_PLATFORM"] = self._orig_env
        if reset_backend_cache is not None:
            try:
                reset_backend_cache()
            except Exception:
                pass
        self._guard.__exit__(None, None, None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def call(self, name, args, approve=False):
        """One funnel call with a FRESH args dict (never reused)."""
        confirm_gate.clear_handler()
        if approve:
            confirm_gate.register_handler(lambda n, s: True)
        try:
            return tools.execute(name, dict(args))
        finally:
            confirm_gate.clear_handler()

    def store(self):
        return receipts_mod._get_store()


class TestDispatchedStateStore(unittest.TestCase):
    """Store-level dispatched-state behaviour (temp stores only)."""

    def test_attempt_starts_undispatched_and_does_not_guard(self):
        store = receipts_mod.ReceiptStore(
            pathlib.Path(tempfile.mkdtemp()) / "r.jsonl")
        attempt = store.record_attempt(
            tool="file_control", args={"path": "/tmp/a"})
        self.assertFalse(attempt.get("dispatched"))
        self.assertIsNone(store.find_unknown(
            tool="file_control", args={"path": "/tmp/a"}))

    def test_marked_attempt_guards_and_survives_reload(self):
        path = pathlib.Path(tempfile.mkdtemp()) / "r.jsonl"
        store = receipts_mod.ReceiptStore(path)
        attempt = store.record_attempt(
            tool="file_control", args={"path": "/tmp/b"})
        self.assertTrue(store.mark_dispatched(attempt["receipt_id"]))
        self.assertIsNotNone(store.find_unknown(
            tool="file_control", args={"path": "/tmp/b"}))
        reloaded = receipts_mod.ReceiptStore(path)
        self.assertIsNotNone(reloaded.find_unknown(
            tool="file_control", args={"path": "/tmp/b"}))

    def test_mark_unknown_id_false_and_resolved_true_without_new_line(self):
        path = pathlib.Path(tempfile.mkdtemp()) / "r.jsonl"
        store = receipts_mod.ReceiptStore(path)
        self.assertFalse(store.mark_dispatched("no-such-id"))
        attempt = store.record_attempt(
            tool="file_control", args={"path": "/tmp/c"})
        store.record_outcome(attempt["receipt_id"], status="succeeded")
        before = path.read_text(encoding="utf-8").count("\n")
        self.assertTrue(store.mark_dispatched(attempt["receipt_id"]))
        after = path.read_text(encoding="utf-8").count("\n")
        self.assertEqual(before, after)

    def test_old_field_less_record_still_guards_exact_only(self):
        tmp = pathlib.Path(tempfile.mkdtemp()) / "old.jsonl"
        sig = receipts_mod.args_signature(
            "file_control", {"path": "./notes.txt"})
        tmp.write_text(json.dumps({
            "type": "attempt", "receipt_id": "old1",
            "tool": "file_control", "args_signature": sig,
            "args_preview": "", "created_at": 0.0,
            "status": "attempted", "outcome_summary": ""}) + "\n",
            encoding="utf-8")
        store = receipts_mod.ReceiptStore(tmp)
        self.assertIsNotNone(store.find_unknown(
            tool="file_control", args={"path": "./notes.txt"}))
        self.assertIsNone(store.find_unknown(
            tool="file_control", args={"path": "notes.txt"}))

    def test_same_signature_resolution_keeps_other_unknown_guarded(self):
        store = receipts_mod.ReceiptStore(
            pathlib.Path(tempfile.mkdtemp()) / "r.jsonl")
        first = store.record_attempt(
            tool="file_control", args={"path": "/tmp/d"})
        second = store.record_attempt(
            tool="file_control", args={"path": "/tmp/d"})
        store.mark_dispatched(first["receipt_id"])
        store.mark_dispatched(second["receipt_id"])
        store.record_outcome(first["receipt_id"], status="refused")
        self.assertIsNotNone(store.find_unknown(
            tool="file_control", args={"path": "/tmp/d"}))


class TestDispatchedStateFunnel(_Base):
    """Funnel-level guard behaviour through the real execute path."""

    def test_unmarked_attempt_does_not_guard_retry(self):
        args = {"action": "delete", "path": "/tmp/regression_dispatch_u1"}
        self.store().record_attempt(tool="file_control", args=dict(args))
        out = self.call("file_control", args)
        self.assertEqual(out.get("kind"), "not_confirmed", str(out))

    def test_marked_attempt_guards_with_receipt_id(self):
        args = {"action": "delete", "path": "/tmp/regression_dispatch_m1"}
        attempt = self.store().record_attempt(
            tool="file_control", args=dict(args))
        self.assertTrue(self.store().mark_dispatched(attempt["receipt_id"]))
        out = self.call("file_control", args)
        self.assertEqual(out.get("status"), "refused", str(out))
        self.assertIn("unknown outcome", out.get("message", ""))
        self.assertIn(attempt["receipt_id"], out.get("message", ""))

    def test_marked_guard_survives_same_signature_resolution(self):
        # Probe sequence that exposed the set-discard hole: a plain
        # call resolves, an unmarked seed lets a same-signature
        # call through (confirm refusal, resolves), then the seed
        # is marked — the retry must STILL refuse.
        args = {"action": "delete", "path": "/tmp/regression_dispatch_s1"}
        plain = self.call("file_control", args)
        self.assertEqual(plain.get("kind"), "not_confirmed", str(plain))
        seed = self.store().record_attempt(
            tool="file_control", args=dict(args))
        through = self.call("file_control", args)
        self.assertEqual(through.get("kind"), "not_confirmed", str(through))
        self.assertTrue(self.store().mark_dispatched(seed["receipt_id"]))
        out = self.call("file_control", args)
        self.assertEqual(out.get("status"), "refused", str(out))
        self.assertIn(seed["receipt_id"], out.get("message", ""))

    def test_cancelled_before_dispatch_still_unguarded(self):
        args = {"action": "delete", "path": "/tmp/regression_dispatch_c1"}
        attempt = self.store().record_attempt(
            tool="file_control", args=dict(args))
        self.store().mark_dispatched(attempt["receipt_id"])
        self.store().record_outcome(
            attempt["receipt_id"], status="cancelled", dispatched=False)
        out = self.call("file_control", args)
        self.assertEqual(out.get("kind"), "not_confirmed", str(out))

    def test_mark_failure_refuses_non_read(self):
        # The confirm gate runs BEFORE the mark, so an unapproved
        # confirm-gated call never reaches the mark — approve the
        # call (the mark failure still refuses before dispatch,
        # so nothing runs) to prove the mark-failure refusal.
        args = {"action": "delete", "path": "/tmp/regression_dispatch_f1"}
        with mock.patch.object(
                receipts_mod, "try_mark_dispatched", return_value=False):
            out = self.call("file_control", args, approve=True)
        self.assertEqual(out.get("status"), "refused", str(out))
        self.assertIn("dispatched", out.get("message", ""))


if __name__ == "__main__":
    unittest.main()
