"""Shipped unit tests (v5.45, P-1): schema validation in execute().

Bad arguments are rejected with the validator's exact error shape;
unknown tools return an error dict carrying `message` (and the H8
`kind`). A synthetic probe tool on an isolated registry instance
keeps these tests independent of the live tool catalogue.

Stdlib unittest only. Run via tests/run_tests.py.
"""

from __future__ import annotations

import pathlib
import unittest

try:
    from _activity_guard import (
        ActivityLogGuard, AuditLogGuard, ReceiptLogGuard)
except ImportError:  # run as a package module instead of top-level
    from tests._activity_guard import (  # type: ignore
        ActivityLogGuard, AuditLogGuard, ReceiptLogGuard)

try:
    from _budget_isolation import restore_budget, snapshot_budget
except ImportError:  # run as a package module instead of top-level
    from tests._budget_isolation import (  # type: ignore
        restore_budget, snapshot_budget)

try:
    from core.funnel import tools_registry as tools_mod  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
except Exception as exc:  # noqa: BLE001
    raise unittest.SkipTest(f"tools unavailable: {exc}")  # Phase 4 S1c INTENTIONAL: shim deleted; message names the tool surface

_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parent.parent

_PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "count": {"type": "integer"},
        "label": {"type": "string"},
        "mode": {"type": "string", "enum": ["fast", "slow"]},
    },
    "required": ["count"],
}


def _probe_registry(handler=None):
    reg = object.__new__(tools_mod.ToolRegistry)
    reg._tools = {}
    reg.register("unit_probe", "probe", _PROBE_SCHEMA,
                 handler or (lambda count, label=None, mode=None:
                             f"count={count}"))
    return reg


# Audit fix Batch 5 (TEST-01 + TEST-02): module fixture.
# TEST-01 — the timeout leg records an UNKNOWN-outcome receipt
# (C10), so the suite restores data/tool_receipts.jsonl to its
# module-entry baseline BEFORE its tests run (setUpModule and
# every ExecuteShapeTests.setUp), not just after (tearDownModule
# restores the same baseline). The file restore alone is not
# enough in-process: the funnel's receipt store is process-global
# and loads once, so the restore also calls
# tool_receipts.reset_default() so the next dispatch reloads
# from the restored file. TEST-02 — the same fixture carries
# the L3 budget-isolation guest rule (snapshot/restore via
# _budget_isolation), so this suite neither inherits nor leaks
# a tripped 120/60s window. Production budget and C10 guard
# are untouched; only this suite's hygiene changed.
_BUDGET_SNAPSHOT = None
_RECEIPT_BASELINE = None


def _receipt_path():
    return _PACKAGE_ROOT / "data" / "tool_receipts.jsonl"


def _restore_receipts_to_baseline():
    """Restore the receipt file to the module-entry baseline
    and reset the funnel's in-memory receipt store, so a
    timeout leg left UNKNOWN by an earlier run no longer
    refuses the identical leg. No-op when already at
    baseline; the baseline itself is restored verbatim by
    tearDownModule, so no real receipt data is ever lost."""
    global _RECEIPT_BASELINE
    path = _receipt_path()
    if _RECEIPT_BASELINE is None:
        # setUpModule did not run (direct class use): capture
        # the current state as the baseline rather than
        # inventing one.
        _RECEIPT_BASELINE = (
            path.exists(),
            path.read_bytes() if path.exists() else None)
    existed, blob = _RECEIPT_BASELINE
    if existed:
        path.write_bytes(blob or b"")
    elif path.exists():
        path.unlink()
    from core import tool_receipts
    tool_receipts.reset_default()


def setUpModule():
    global _BUDGET_SNAPSHOT, _RECEIPT_BASELINE
    _BUDGET_SNAPSHOT = snapshot_budget()
    path = _receipt_path()
    _RECEIPT_BASELINE = (
        path.exists(), path.read_bytes() if path.exists() else None)
    _restore_receipts_to_baseline()


def tearDownModule():
    global _BUDGET_SNAPSHOT
    _restore_receipts_to_baseline()
    if _BUDGET_SNAPSHOT is not None:
        restore_budget(_BUDGET_SNAPSHOT)
        _BUDGET_SNAPSHOT = None


class ValidateArgsTests(unittest.TestCase):
    def setUp(self):
        self.reg = _probe_registry()

    def test_valid_args_pass(self):
        self.assertIsNone(
            self.reg.validate_args("unit_probe", {"count": 3}))
        self.assertIsNone(self.reg.validate_args(
            "unit_probe", {"count": 3, "label": "x", "mode": "fast"}))

    def test_missing_required_exact_message(self):
        self.assertEqual(
            self.reg.validate_args("unit_probe", {}),
            "Invalid arguments for tool 'unit_probe': "
            "'count' is required but was not provided")

    def test_wrong_type_exact_message(self):
        self.assertEqual(
            self.reg.validate_args("unit_probe", {"count": "three"}),
            "Invalid arguments for tool 'unit_probe': "
            "'count' must be an integer (got str)")

    def test_bool_is_not_an_integer(self):
        self.assertEqual(
            self.reg.validate_args("unit_probe", {"count": True}),
            "Invalid arguments for tool 'unit_probe': "
            "'count' must be an integer (got bool)")

    def test_enum_violation_exact_message(self):
        self.assertEqual(
            self.reg.validate_args(
                "unit_probe", {"count": 1, "mode": "warp"}),
            "Invalid arguments for tool 'unit_probe': "
            "'mode' must be one of ['fast', 'slow'] (got 'warp')")

    def test_non_dict_args_exact_message(self):
        self.assertEqual(
            self.reg.validate_args("unit_probe", [1, 2]),
            "Invalid arguments for tool 'unit_probe': "
            "arguments must be an object (got list)")

    def test_unknown_tool_message(self):
        self.assertEqual(self.reg.validate_args("nope_tool", {}),
                         "Unknown tool: nope_tool")

    def test_null_for_typed_field_rejected_open_extras_pass(self):
        # Audit fix A4 (P1-2) — INTENTIONAL BEHAVIOUR CHANGE,
        # replacing the old "null counts as absent" pin: an
        # explicit null for a typed non-nullable field is now
        # rejected (JSON null is a distinct type), while extras
        # still pass for a genuinely OPEN schema (the JSON
        # Schema default is allowed; only a closed schema
        # rejects extras).
        # Audit fix Batch 2 (TOOL-02) — INTENTIONAL PIN CHANGE,
        # disclosed: register() now CLOSES every fixed-signature
        # schema (additionalProperties: false), so this probe —
        # registered through register() — rejects extras, even
        # though its literal schema declares nothing. The A4
        # open-schema behaviour itself is unchanged and is
        # pinned below by validating the same literal schema
        # directly (bypassing register's close), as the A4
        # validator tests do.
        self.assertIsNotNone(self.reg.validate_args(
            "unit_probe", {"count": 1, "extra": 9}))
        self.assertIsNotNone(self.reg.validate_args(
            "unit_probe", {"count": 1, "label": None}))
        from core.funnel.tools_registry import _validate_args_against_schema  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        self.assertIsNone(_validate_args_against_schema(
            "unit_probe", {"count": 1, "extra": 9}, _PROBE_SCHEMA))


class ExecuteShapeTests(unittest.TestCase):
    def setUp(self):
        # TEST-01 restore-BEFORE: clear any UNKNOWN receipt a
        # previous run of this module left, before this test
        # dispatches anything (see the module fixture above).
        _restore_receipts_to_baseline()
        self._guard = ActivityLogGuard(_PACKAGE_ROOT)
        self._guard.__enter__()
        # v5.50 R5-T: execute() also appends one audit record
        # per call to data/audit.jsonl (core/audit.py, v5.49
        # item 38) — this class guarded activity.log only, so
        # its 6 execute() calls leaked a 1,314-byte audit.jsonl
        # into the REAL tree's data/ on every runner run (the
        # baseline has no audit.jsonl at all). Guard it too,
        # with the same snapshot/restore coverage.
        self._audit_guard = AuditLogGuard(_PACKAGE_ROOT)
        self._audit_guard.__enter__()
        self.reg = _probe_registry()

    def tearDown(self):
        self._audit_guard.__exit__(None, None, None)
        self._guard.__exit__(None, None, None)

    def test_bad_args_rejected_with_exact_error_shape(self):
        out = self.reg.execute("unit_probe", {"count": "three"})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
        self.assertEqual(out["status"], "refused")
        self.assertEqual(out["kind"], "validation")
        self.assertEqual(
            out["message"],
            "Invalid arguments for tool 'unit_probe': "
            "'count' must be an integer (got str)")
        self.assertTrue(out["validation_error"])

    def test_unknown_tool_error_dict_carries_message(self):
        out = self.reg.execute("nope_tool", {})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "failure" for unknown/unroutable calls (a failure, not a refusal); kind/message unchanged.
        self.assertEqual(out["status"], "failure")
        self.assertIn("message", out)
        self.assertEqual(out["message"], "Unknown tool: nope_tool")
        self.assertEqual(out["kind"], "unavailable")

    def test_good_call_succeeds(self):
        out = self.reg.execute("unit_probe", {"count": 7})
        self.assertEqual(out, {"status": "success", "result": "count=7"})

    def test_handler_error_kinds(self):
        def raise_timeout(**kw):
            raise TimeoutError("too slow")

        def raise_missing(**kw):
            raise FileNotFoundError("gone")

        def raise_value(**kw):
            raise ValueError("bad math")

        # L3: INTENTIONAL assertion update, not a weakening —
        # the funnel never emits legacy status "error" since
        # audit A5 (core/funnel/tool_result.py): _audited() normalizes
        # by kind — refusal kinds (timeout cool-down) map to
        # "refused", failure kinds (unavailable, handler_error)
        # map to "failure". Kind and message prefix are unchanged
        # (the sibling tests in this file already pin the same
        # A5 mapping for validation/unavailable). A handler
        # raising TimeoutError lands here as a cool-down
        # refusal; a genuine deadline timeout passes status
        # "timeout" directly and never reaches this mapping.
        # Each handler gets DISTINCT args: the Phase 2 C10 guard
        # keys on (tool, args-signature), and the timeout leg's
        # outcome records as UNKNOWN — with identical args the
        # guard would (correctly, per its contract) refuse legs
        # 2 and 3 without dispatching. Standing C10 rule, same
        # as the A5 timeout test: repeat runs without a data
        # restore refuse the timeout leg; verification restores
        # data before runs containing timeout tests.
        for handler, kind, status, count in (
                (raise_timeout, "timeout", "refused", 1),
                (raise_missing, "unavailable", "failure", 2),
                (raise_value, "handler_error", "failure", 3)):
            reg = _probe_registry(handler)
            out = reg.execute("unit_probe", {"count": count})
            self.assertEqual(out["status"], status)
            self.assertEqual(out["kind"], kind)
            self.assertTrue(out["message"].startswith("Execution failed: "))

    def test_timeout_leg_repeatable_after_baseline_restore(self):
        # Batch 5 (TEST-01): pins the restore-BEFORE rule
        # end to end, without weakening the receipt assertion.
        # The timeout leg records UNKNOWN; the identical leg
        # is then (correctly) refused by the C10 guard; after
        # the baseline restore the identical leg dispatches
        # again as a timeout. If the restore were dropped, the
        # third dispatch would come back kind "refused" and
        # this test would fail — exactly the audit's repeat
        # failure, reproduced in miniature.
        def raise_timeout(**kw):
            raise TimeoutError("too slow")

        first = _probe_registry(raise_timeout).execute(
            "unit_probe", {"count": 1})
        self.assertEqual(first["status"], "refused")
        self.assertEqual(first["kind"], "timeout")
        refused = _probe_registry(raise_timeout).execute(
            "unit_probe", {"count": 1})
        self.assertEqual(refused["status"], "refused")
        self.assertEqual(refused["kind"], "refused")
        _restore_receipts_to_baseline()
        again = _probe_registry(raise_timeout).execute(
            "unit_probe", {"count": 1})
        self.assertEqual(again["status"], "refused")
        self.assertEqual(again["kind"], "timeout")


if __name__ == "__main__":
    unittest.main()
