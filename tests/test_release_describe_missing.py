"""Tests for field-check fix #25 (round-2 N1 BUG 2): release_describe
with no record_json must refuse honestly, in plain words, naming the
missing argument — never leak a TypeError payload.

What is proven here:
* kind "route" and kind "train" with no record_json refuse with
  "record_json is required: kind '<kind>' needs ..." — the pre-fix
  behaviour was a leaked "TypeError: describe_route() missing 1
  required positional argument: 'routes'" (and the same shape for
  describe_train / 'gates');
* kind "verify" with no record_json refuses the same uniform way
  (it was already leak-free pre-fix — its fields all default — but
  its message named only the version, not the missing record_json);
* the same leak one layer down is closed: release_spine.describe()
  called with no fields refuses with the module's own validation
  ("routes must be a non-empty list" / "gates must be a non-empty
  list" / "version must be a non-empty string") instead of raising
  TypeError;
* valid records still describe, bad JSON and empty routes still
  refuse honestly, and an out-of-enum kind is refused by the
  funnel's schema validation before the handler runs.
"""

import json
import unittest

from core import release_spine as release_mod
from core.funnel.tools_registry import tools

try:
    from _budget_isolation import restore_budget, snapshot_budget
except ImportError:  # run as a package module instead of top-level
    from tests._budget_isolation import (  # type: ignore
        restore_budget, snapshot_budget)


def _result(executed):
    if isinstance(executed, dict) and "result" in executed:
        value = executed["result"]
        return json.loads(value) if isinstance(value, str) else value
    return executed


GOOD_HASH = "a" * 64


class MissingRecordTests(unittest.TestCase):
    """Funnel-level: no record_json at all."""

    def test_route_no_record_refuses_without_typeerror(self):
        data = _result(tools.execute("release_describe",
                                     {"kind": "route"}))
        self.assertFalse(data["ok"], data)
        self.assertNotIn("TypeError", data["reason"], data)
        self.assertIn("record_json", data["reason"], data)
        self.assertIn("routes", data["reason"], data)

    def test_train_no_record_refuses_without_typeerror(self):
        data = _result(tools.execute("release_describe",
                                     {"kind": "train"}))
        self.assertFalse(data["ok"], data)
        self.assertNotIn("TypeError", data["reason"], data)
        self.assertIn("record_json", data["reason"], data)
        self.assertIn("gates", data["reason"], data)

    def test_verify_no_record_refuses_without_typeerror(self):
        data = _result(tools.execute("release_describe",
                                     {"kind": "verify"}))
        self.assertFalse(data["ok"], data)
        self.assertNotIn("TypeError", data["reason"], data)
        self.assertIn("record_json", data["reason"], data)
        self.assertIn("version", data["reason"], data)


class ModuleMissingFieldTests(unittest.TestCase):
    """Module-level: describe() with no fields must refuse,
    not raise."""

    def test_describe_route_no_fields_refuses(self):
        data = release_mod.describe("route")
        self.assertFalse(data["ok"], data)
        self.assertEqual(data["reason"],
                         "routes must be a non-empty list")

    def test_describe_train_no_fields_refuses(self):
        data = release_mod.describe("train")
        self.assertFalse(data["ok"], data)
        self.assertEqual(data["reason"],
                         "gates must be a non-empty list")

    def test_describe_verify_no_fields_refuses(self):
        data = release_mod.describe("verify")
        self.assertFalse(data["ok"], data)
        self.assertEqual(data["reason"],
                         "version must be a non-empty string")


class MissingRecordControlsTests(unittest.TestCase):
    """Valid and invalid records behave as before."""

    def test_valid_records_still_describe(self):
        route = _result(tools.execute("release_describe", {
            "kind": "route",
            "record_json": json.dumps(
                {"routes": ["zip over folder"]})}))
        self.assertTrue(route["ok"], route)
        verify = _result(tools.execute("release_describe", {
            "kind": "verify",
            "record_json": json.dumps(
                {"version": "5.92.0", "manifest_present": True,
                 "sha256": GOOD_HASH,
                 "data_baseline_bytes": 369701})}))
        self.assertTrue(verify["shape_ok"], verify)
        train = _result(tools.execute("release_describe", {
            "kind": "train",
            "record_json": json.dumps(
                {"gates": [{"name": "battery",
                            "verdict": "green"}]})}))
        self.assertTrue(train["ok"], train)

    def test_bad_json_still_refuses(self):
        data = _result(tools.execute("release_describe", {
            "kind": "route", "record_json": "{not json"}))
        self.assertFalse(data["ok"], data)
        self.assertIn("not valid JSON", data["reason"], data)
        self.assertNotIn("TypeError", data["reason"], data)

    def test_empty_routes_still_refuses(self):
        data = _result(tools.execute("release_describe", {
            "kind": "route",
            "record_json": json.dumps({"routes": []})}))
        self.assertFalse(data["ok"], data)
        self.assertEqual(data["reason"],
                         "routes must be a non-empty list")

    def test_bad_kind_refused_before_handler(self):
        # The funnel's schema validation refuses an out-of-enum
        # kind before the handler runs.
        result = tools.execute("release_describe",
                               {"kind": "nope"})
        self.assertEqual(result["status"], "refused", result)
        self.assertEqual(result["kind"], "validation", result)
        self.assertNotIn("TypeError", json.dumps(result))


# Same budget-isolation shape as test_release_w10.py: this suite
# executes tools through the shared funnel, so it snapshots the
# budget window + clock, opens a clean window, and restores
# byte-for-byte, instead of tripping on ordering.
_BUDGET_SNAPSHOT = None


def setUpModule():
    global _BUDGET_SNAPSHOT
    _BUDGET_SNAPSHOT = snapshot_budget()


def tearDownModule():
    global _BUDGET_SNAPSHOT
    if _BUDGET_SNAPSHOT is not None:
        restore_budget(_BUDGET_SNAPSHOT)
        _BUDGET_SNAPSHOT = None


if __name__ == "__main__":
    unittest.main()
