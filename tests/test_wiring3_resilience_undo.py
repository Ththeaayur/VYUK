"""Wiring Build 3 tests (stdlib unittest, no GUI, no network).

The Doctor page itself is Qt (gui/doctor_page.py) and cannot
render in this lab (no WebEngine, libEGL missing), so no render
is claimed here — exactly as in the Wiring Build 1 tests. What
IS tested, Qt-free, is everything the Doctor buttons drive,
plus the loop gate they share a trip signal with:

- the shared gate: trip latches, rearm refuses without human
  confirmation and without approval, and a confirmed human
  re-arm clears the latch (but reports a still-tripped failsafe
  honestly instead of claiming control resumed);
- the existing status renderer, reached through the shared
  gate payload, says TRIPPED while tripped and healthy after
  re-arm — the same payload the Doctor page renders;
- planner.run_goal halts at the step boundary on a trip, via
  an explicit callable AND via the shared gate with no kwarg,
  executing no action at all, and runs normally when untripped;
- the undo button's mechanism: the last undoable recorded
  change is undone through its registered restorer, confirm-
  gating refuses unapproved undo, a non-undoable latest change
  is skipped (and listed) rather than forced, a change with no
  restorer is reported and preserved rather than pretended
  undone, and a tripped failsafe refuses the undo;
- source-level wiring: doctor_page.py actually contains the
  Resilience card, both buttons, both handlers, the confirm
  dialogs (default No), and the gate import — so the buttons
  are not a claim about a file that does not mention them.

The real app failsafe is tripped only inside one test, with
reset() in a finally block, and every test resets the shared
gate in setUp/tearDown so no latch leaks between tests.
"""

import asyncio
import os
import tempfile
import unittest

from core import failsafe as _failsafe
from core.brain import planner as _planner  # Phase 3 B2-remainder INTENTIONAL: planner moved to core/brain/
from core.resilience import resilience_gate as gate  # INTENTIONAL (Phase 3 R1): moved to core/resilience/


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class _Base(unittest.TestCase):
    def setUp(self):
        gate.reset_for_tests()
        try:
            _failsafe.reset()
        except Exception:  # noqa: BLE001 — untripped already
            pass
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        gate.reset_for_tests()
        try:
            _failsafe.reset()
        except Exception:  # noqa: BLE001
            pass


class TestTripGate(_Base):
    def test_trip_and_confirmed_human_rearm(self):
        gate.configure_resilience(os.path.join(self._tmp.name, "res"),
                                  failsafe_tripped=lambda: False)
        self.assertFalse(gate.is_tripped())
        gate.trip_resilience("test trip")
        self.assertTrue(gate.is_tripped())
        self.assertEqual(gate.trip_reason(), "test trip")
        # Unapproved and non-human re-arms refuse and change nothing.
        self.assertFalse(gate.rearm_resilience(approved=False)["rearmed"])
        self.assertTrue(gate.is_tripped())
        self.assertFalse(
            gate.rearm_resilience(approved=True, human=False)["rearmed"])
        self.assertTrue(gate.is_tripped())
        result = gate.rearm_resilience(approved=True, human=True)
        self.assertTrue(result["rearmed"])
        self.assertTrue(result["armed"])
        self.assertFalse(result["failsafe_still_tripped"])
        self.assertFalse(gate.is_tripped())

    def test_rearm_reports_still_tripped_failsafe(self):
        gate.configure_resilience(os.path.join(self._tmp.name, "res"),
                                  failsafe_tripped=lambda: True)
        gate.trip_resilience("test trip")
        result = gate.rearm_resilience(approved=True, human=True)
        self.assertTrue(result["rearmed"])
        self.assertTrue(result["failsafe_still_tripped"])
        self.assertFalse(result["armed"])
        self.assertIn("failsafe", result["reason"])
        # The shared wiring latch is cleared even though the
        # failsafe still holds control paused — that is the
        # honest split between the two latches.
        self.assertTrue(gate.is_tripped())

    def test_status_renderer_says_tripped_then_healthy(self):
        gate.configure_resilience(os.path.join(self._tmp.name, "res"),
                                  failsafe_tripped=lambda: False)
        gate.trip_resilience("renderer trip")
        lines = gate.render_status_lines()
        self.assertTrue(any("TRIPPED" in line for line in lines))
        self.assertTrue(any("renderer trip" in line for line in lines))
        gate.rearm_resilience(approved=True, human=True)
        lines = gate.render_status_lines()
        self.assertTrue(any("healthy" in line for line in lines))
        self.assertFalse(any("TRIPPED" in line for line in lines))

    def test_unconfigured_status_is_honest(self):
        lines = gate.render_status_lines()
        self.assertTrue(any("not yet configured" in line for line in lines))


class TestLoopTripGate(_Base):
    def _picker(self, calls):
        async def pick(goal, observation, history):
            calls.append(goal)
            return {"done": "picked done"}
        return pick

    def test_loop_halts_on_explicit_trip_callable(self):
        calls = []
        report = _run(_planner.run_goal(
            "do a thing", pick_action=self._picker(calls),
            resilience_tripped=lambda: True))
        self.assertEqual(calls, [])
        self.assertIn("resilience tripped", report)

    def test_loop_halts_on_shared_gate_trip_without_kwarg(self):
        gate.configure_resilience(os.path.join(self._tmp.name, "res"),
                                  failsafe_tripped=lambda: False)
        gate.trip_resilience("doctor trip")
        calls = []
        report = _run(_planner.run_goal(
            "do a thing", pick_action=self._picker(calls)))
        self.assertEqual(calls, [])
        self.assertIn("resilience tripped", report)
        self.assertIn("re-arms", report)

    def test_loop_runs_when_untripped(self):
        calls = []
        report = _run(_planner.run_goal(
            "do a thing", pick_action=self._picker(calls)))
        self.assertEqual(calls, ["do a thing"])
        self.assertIn("picked done", report)


class TestUndoAction(_Base):
    def _configure_undo(self):
        return gate.configure_undo(os.path.join(self._tmp.name, "undo.json"))

    def test_undo_last_undoes_through_restorer(self):
        self._configure_undo()
        state = {"value": "after"}
        gate.register_undo_restorer(
            "setting changed", lambda before: state.update(before) or True)
        gate.record_undo_change("chg-1", change_class="setting changed",
                                before={"value": "before"}, after={"value": "after"})
        # Confirm-gating: unapproved undo refuses and changes nothing.
        refused = gate.undo_last(approved=False)
        self.assertFalse(refused["undone"])
        self.assertEqual(state["value"], "after")
        result = gate.undo_last(approved=True)
        self.assertTrue(result["undone"])
        self.assertEqual(result["id"], "chg-1")
        self.assertEqual(state["value"], "before")
        # Already undone: nothing left to undo.
        again = gate.undo_last(approved=True)
        self.assertFalse(again["undone"])
        self.assertIn("no undoable", again["reason"])

    def test_non_undoable_latest_is_skipped_and_listed(self):
        self._configure_undo()
        state = {"value": "after"}
        gate.register_undo_restorer(
            "setting changed", lambda before: state.update(before) or True)
        gate.record_undo_change("chg-1", change_class="setting changed",
                                before={"value": "before"}, after={"value": "after"})
        gate.record_undo_change("chg-2",
                                change_class="message sent to another person",
                                before=None, after=None)
        result = gate.undo_last(approved=True)
        self.assertTrue(result["undone"])
        self.assertEqual(result["id"], "chg-1")
        self.assertEqual([s["id"] for s in result["skipped_non_undoable"]],
                         ["chg-2"])
        self.assertEqual(state["value"], "before")
        # Now only the non-undoable record remains: refuse plainly.
        again = gate.undo_last(approved=True)
        self.assertFalse(again["undone"])
        self.assertIn("cannot be taken back", again["reason"])

    def test_no_restorer_is_reported_not_pretended(self):
        self._configure_undo()
        gate.record_undo_change("chg-9", change_class="mystery class",
                                before="b", after="a")
        result = gate.undo_last(approved=True)
        self.assertFalse(result["undone"])
        self.assertIn("no restorer registered", result["reason"])
        # The record is preserved, not marked undone.
        summary = gate.shared_undo_store().summary()
        self.assertFalse(summary[0]["taken_back"])

    def test_tripped_failsafe_refuses_undo(self):
        self._configure_undo()
        state = {"value": "after"}
        gate.register_undo_restorer(
            "setting changed", lambda before: state.update(before) or True)
        gate.record_undo_change("chg-1", change_class="setting changed",
                                before={"value": "before"}, after={"value": "after"})
        try:
            _failsafe.trip_now("undo fence test")
            result = gate.undo_last(approved=True)
        finally:
            _failsafe.reset()
        self.assertFalse(result["undone"])
        self.assertIn("failsafe-tripped", result["reason"])
        self.assertEqual(state["value"], "after")

    def test_unconfigured_undo_is_honest(self):
        result = gate.undo_last(approved=True)
        self.assertFalse(result["undone"])
        self.assertIn("no undo store configured", result["reason"])


class TestDoctorPageWiring(unittest.TestCase):
    def test_doctor_page_contains_resilience_card_and_handlers(self):
        path = os.path.join(os.path.dirname(__file__), "..",
                            "gui", "doctor_page.py")
        with open(os.path.normpath(path), encoding="utf-8") as handle:
            source = handle.read()
        for needle in (
            "resilience_label",
            "btn_resilience_rearm",
            "btn_undo_last",
            "_on_resilience_rearm",
            "_on_undo_last",
            "_refresh_resilience_status",
            "resilience_gate",
            "HudDialog.confirm",
            "Re-arm resilience",
            "Undo last change",
        ):
            self.assertIn(needle, source, needle)
        # Confirm dialogs default to No (C-14 approve-first):
        # no confirm call opts into a Yes default (the per-call
        # proof lives in test_uiinteraction's confirm scanner).
        self.assertNotIn("default=True", source)
        # No new sidebar entry and no stack renumbering in this file.
        self.assertNotIn("_show_page(9", source)


if __name__ == "__main__":
    unittest.main()
