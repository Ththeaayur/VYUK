"""Test-isolation batch: the harness must never contaminate global
rate-limit / confirmation state across cases.

What this suite pins:

* ``core.funnel.tools_handlers.reset_tool_budget()`` — the ONE supported
  budget-reset helper (shared location, never copied per-suite).
  RULE (also in its docstring): any suite that executes tools
  through the funnel must call it in setUpModule AND
  tearDownModule, so its tests neither inherit the previous
  suites' dispatches nor leave a window behind.
* The existing injectable clock seam ``tools_handlers._budget_now``
  (suites replace it to travel in time) — used here to prove the
  60 s window ages out; the scheduler's equivalent seam is
  ``Scheduler._now_fn``. No scheduler timing trips in the baseline
  full run (zero scheduler FAIL lines), so the budget window is
  the timing this batch covers.
* The A5 structured-result contract, as DECIDED for this codebase:
  "result" present ⟺ success payload present; refusals carry
  status/kind/message and NO "result" key. A blanket empty
  "result" on refusals was tried first and REVERTED with evidence:
  in the first testiso full run it broke routerfinish's
  tripped-payload test and firstrun's _result helper (both branch
  on key-presence to detect a payload), and the same pattern is
  used by ~13 wiring suites. Consumers must therefore use .get()
  and branch on status/kind, never index ["result"] on a
  possibly-refused result — which is exactly how the triage3 /
  triage4 consumer fix works (skip on kind == "budget").
* Two consecutive simulated sessions leave no leaked confirmation
  or budget state behind.

CRITICAL guest rule for this suite (learned from the first
testiso full run): this suite must leave global state EXACTLY as
it found it. A plain reset in tearDownModule drained the
full run's saturated budget window mid-run and collapsed the
failure profile from 55 lines to 4 — masking the tracked budget
fragility class instead of isolating this suite. So setUpModule
SNAPSHOTS the budget timestamps, the budget clock, and the
confirm handler (the routerfinish snapshot/restore precedent),
and tearDownModule RESTORES them byte-for-byte. Per-test resets
give this suite its own clean window; the module restore gives
the rest of the run its window back untouched.

This suite itself resets the budget in setUp AND tearDown of
every test, so its own dispatches can never trip a later test.
Production behavior is unchanged: the 120 calls / 60 s budget
itself is exactly as-is; only test-harness reset/clock seams are
exercised.
"""

import json
import os
import tempfile
import unittest

from core import confirm_gate
from core.funnel import tool_result as tr_mod  # Phase 3 F2a INTENTIONAL: tool_result moved to core/funnel/
from core.funnel import tools_handlers as th  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/


def _save_handler():
    """The confirm handler as found (None when none registered)."""
    return confirm_gate._handler


def _restore_handler(saved):
    if saved is None:
        confirm_gate.clear_handler()
    else:
        confirm_gate.register_handler(saved)


_SAVED = {}


def setUpModule():
    # Snapshot global state FIRST (routerfinish precedent), then
    # open a clean window for this suite's own tests. tearDownModule
    # restores the snapshot, so the rest of the run sees exactly
    # the state it would have seen without this suite.
    _SAVED["budget"] = list(th._budget_timestamps)
    _SAVED["now"] = th._budget_now
    _SAVED["handler"] = _save_handler()
    th.reset_tool_budget()


def tearDownModule():
    th._budget_timestamps[:] = _SAVED["budget"]
    th._budget_now = _SAVED["now"]
    _restore_handler(_SAVED["handler"])


class TestIsoTests(unittest.TestCase):
    def setUp(self):
        th.reset_tool_budget()
        self._orig_now = th._budget_now
        self._orig_handler = _save_handler()

    def tearDown(self):
        th._budget_now = self._orig_now
        th.reset_tool_budget()
        _restore_handler(self._orig_handler)

    def _trip(self):
        """Fill the budget window through the real check."""
        limit = th._tool_call_budget()
        for _ in range(limit + 10):
            th._budget_check("testiso_probe", True)

    def test_reset_helper_clears_tripped_window(self):
        self._trip()
        self.assertIsNotNone(th._budget_check("testiso_probe", True))
        th.reset_tool_budget()
        self.assertIsNone(th._budget_check("testiso_probe", True))

    def test_reset_is_noop_when_fresh(self):
        th.reset_tool_budget()
        self.assertIsNone(th._budget_check("testiso_probe", True))

    def test_clock_injection_ages_out_window(self):
        now = [1000.0]
        th._budget_now = lambda: now[0]
        try:
            limit = th._tool_call_budget()
            for _ in range(limit):
                self.assertIsNone(th._budget_check("testiso_probe", True))
            self.assertIsNotNone(th._budget_check("testiso_probe", True))
            # Travel past the 60 s window: every stamp ages out.
            now[0] = 1000.0 + 61.0
            self.assertIsNone(th._budget_check("testiso_probe", True))
        finally:
            th._budget_now = self._orig_now

    def test_tripped_execute_detectable_without_keyerror(self):
        # Exact triage3/triage4 shapes: a budget-tripped funnel
        # result is a refusal carrying status/kind/message and NO
        # "result" key (the decided contract — see module docstring
        # for why a blanket empty "result" was reverted). The
        # refusal is detectable via .get() and kind alone, with no
        # KeyError — which is exactly how the triage consumer fix
        # branches (skip on kind == "budget").
        self._trip()
        confirm_gate.clear_handler()
        result = tools.execute("diagnostics_preview", {
            "sections_json": json.dumps([
                {"name": "logs", "content": "hello"}]),
        })
        self.assertEqual(result.get("kind"), "budget")
        self.assertEqual(result.get("status"), "refused")
        self.assertIn("message", result)
        self.assertIsNone(result.get("result"))
        self.assertNotIn("result", result)

    def test_normalize_keeps_refusals_without_result(self):
        # Contract pin (see module docstring): refusals gain NO
        # "result" key — key-presence is the success signal. A
        # blanket empty "result" broke routerfinish's and firstrun's
        # result-style helpers in the first testiso full run.
        out = tr_mod.normalize_result(
            {"status": "error", "kind": "budget", "message": "m"})
        self.assertEqual(out["status"], "refused")
        self.assertNotIn("result", out)
        out = tr_mod.normalize_result(
            {"status": "refused", "kind": "validation", "message": "m"})
        self.assertNotIn("result", out)
        out = tr_mod.normalize_result(
            {"status": "success", "result": "ok"})
        self.assertEqual(out["result"], "ok")
        # Non-dicts and statusless dicts are never given a field:
        # the normalizer must not invent one for foreign data.
        self.assertEqual(tr_mod.normalize_result("plain"), "plain")
        self.assertEqual(
            tr_mod.normalize_result({"kind": "budget"}),
            {"kind": "budget"})

    def test_two_sessions_no_leaked_state(self):
        # Session 1: approving handler + a tripped budget.
        confirm_gate.register_handler(lambda name, summary: True)
        try:
            self._trip()
            self.assertIsNotNone(th._budget_check("testiso_probe", True))
        finally:
            confirm_gate.clear_handler()
        # Session boundary: the documented reset pair.
        th.reset_tool_budget()
        confirm_gate.clear_handler()
        # Session 2, no handler: a confirm-gated write is refused
        # (proves session 1's approving handler did NOT leak), and
        # a read proceeds (proves the budget is clean).
        with tempfile.TemporaryDirectory() as tmp:
            result = tools.execute("study_plan", {
                "workspace_dir": os.path.join(tmp, "study"),
                "goals_json": "[]",
                "topics_json": "[]",
            })
            self.assertEqual(result.get("kind"), "not_confirmed")
            self.assertFalse(os.path.exists(
                os.path.join(tmp, "study", "plan.json")))
            result = tools.execute("diagnostics_preview", {
                "sections_json": json.dumps([
                    {"name": "logs", "content": "hello"}]),
            })
            payload = json.loads(result["result"])
            self.assertEqual(payload["status"], "ok")
        # Session 2, with handler: the gated write proceeds.
        confirm_gate.register_handler(lambda name, summary: True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = tools.execute("study_plan", {
                    "workspace_dir": os.path.join(tmp, "study"),
                    "goals_json": json.dumps(["BB101"]),
                    "topics_json": json.dumps([
                        {"key": "l10", "title": "L10",
                         "prerequisites": []}]),
                })
                payload = json.loads(result["result"])
                self.assertEqual(payload["status"], "ok")
                self.assertTrue(os.path.exists(
                    os.path.join(tmp, "study", "plan.json")))
        finally:
            confirm_gate.clear_handler()


if __name__ == "__main__":
    unittest.main()
