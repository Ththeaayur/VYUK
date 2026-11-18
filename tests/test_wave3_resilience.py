"""Wave 3 Resilience wiring tests (stdlib unittest, no GUI, no network).

Covers: trip latching + human-only re-arm, zero dispatches when
tripped (including mid-replay), exactly-once ledger, sleep never
feeding crash counting, owed-read ledger (nothing cleared),
and the GUI status renderer (pure text, no Qt).
"""

import tempfile
import unittest

from core.resilience import resilience_wiring as wiring  # INTENTIONAL (Phase 3 R1): moved to core/resilience/
from gui import resilience_status as status_gui


class TripTests(unittest.TestCase):
    def test_trip_latches_and_human_only_rearm(self):
        w = wiring.ResilienceWiring(tempfile.mkdtemp())
        self.assertFalse(w.is_tripped())
        w.trip("test trip")
        self.assertTrue(w.is_tripped())
        self.assertFalse(w.rearm(human=False)["rearmed"])
        self.assertTrue(w.is_tripped())
        self.assertTrue(w.rearm(human=True)["rearmed"])
        self.assertFalse(w.is_tripped())

    def test_tripped_means_zero_dispatches(self):
        w = wiring.ResilienceWiring(tempfile.mkdtemp())
        w.enqueue_outbox({"kind": "note", "body": "hi"})
        w.trip("test trip")
        seen = []
        result = w.deliver_outbox(seen.append)
        self.assertEqual(result["dispatched"], 0)
        self.assertEqual(seen, [])

    def test_mid_replay_trip_stops_delivery(self):
        w = wiring.ResilienceWiring(tempfile.mkdtemp())
        for i in range(3):
            w.enqueue_outbox({"kind": "note", "n": i})

        def funnel(entry):
            if len(seen) == 1:
                w.trip("mid-replay trip")
            seen.append(entry)
            return True

        seen = []
        result = w.deliver_outbox(funnel)
        # First dispatch happened, then the trip stopped the pass:
        # the third entry must never have been dispatched.
        self.assertEqual(result["reason"], "failsafe-tripped-mid-replay")
        self.assertEqual(len(seen), 2)

    def test_clean_delivery_acks(self):
        w = wiring.ResilienceWiring(tempfile.mkdtemp())
        w.enqueue_outbox({"kind": "note", "body": "hi"})
        seen = []
        result = w.deliver_outbox(lambda e: seen.append(e) or True)
        self.assertEqual(result["acked"], 1)
        self.assertEqual(len(seen), 1)


class LedgerTests(unittest.TestCase):
    def test_exactly_once_replay_returns_original(self):
        w = wiring.ResilienceWiring(tempfile.mkdtemp())
        first = w.effect_once("k1", {"done": True})
        second = w.effect_once("k1", {"done": False})
        self.assertTrue(first["recorded"])
        self.assertFalse(second["recorded"])
        self.assertEqual(second["outcome"], {"done": True})

    def test_sleep_never_feeds_crash_counting(self):
        w = wiring.ResilienceWiring(tempfile.mkdtemp())
        last = {"session_id": "s1", "wall": 1000.0, "monotonic": 100.0}
        now = {"session_id": "s1", "wall": 2000.0, "monotonic": 100.0}
        verdict = w.classify_wake(last, now)["verdict"]
        self.assertEqual(verdict, "sleep")
        self.assertFalse(w.record_crash(verdict=verdict)["recorded"])

    def test_crash_verdict_records(self):
        w = wiring.ResilienceWiring(tempfile.mkdtemp())
        last = {"session_id": "s1", "wall": 1000.0, "monotonic": 100.0}
        now = {"session_id": "s2", "wall": 1001.0, "monotonic": 101.0}
        verdict = w.classify_wake(last, now)["verdict"]
        self.assertEqual(verdict, "crash")
        self.assertTrue(w.record_crash(verdict=verdict)["recorded"])


class OwedReadTests(unittest.TestCase):
    def test_nothing_cleared(self):
        # Sweep Build 6 supersedes the old blanket assertion for five
        # items: their primary licence reads were filed (sdist = wheel)
        # and three were wired as OPTIONAL imports, two were read and
        # deliberately NOT wired. The remaining items are unchanged:
        # still parked / pilot-only, still not cleared.
        clearance = wiring.owed_read_clearance()
        for name in ("DiskCache", "whenever", "aiohappyeyeballs"):
            self.assertEqual(clearance[name]["ship_state"], "wired_optional")
        for name in ("exactly-once", "agent-saga"):
            self.assertEqual(clearance[name]["ship_state"], "not_wired")
        for name in ("APSW", "Litestream", "pycaw"):
            self.assertIn(name, clearance)
            self.assertIn(clearance[name]["ship_state"],
                          ("parked", "pilot-only"))

    def test_status_payload_lists_libraries_and_fences(self):
        w = wiring.ResilienceWiring(tempfile.mkdtemp())
        status = w.status()
        self.assertEqual(len(status["libraries"]), 7)
        # INTENTIONAL (Phase 2 B3 rename): status payload lists current module names (was "rl_outbox_v575").
        self.assertIn("resilience_outbox", status["libraries"])
        self.assertTrue(any("zero dispatches" in f for f in status["fences"]))
        lines = status_gui.render_resilience_status(status)
        self.assertTrue(any("Resilience wiring" in line for line in lines))

    def test_lab_kill_drill_never_counts_as_field_proof(self):
        w = wiring.ResilienceWiring(tempfile.mkdtemp())
        entry = w.record_kill_drill(passed=True, steps=["trip", "replay"],
                                    run_by="lab")
        self.assertFalse(entry["counts_as_field_proof"])
        text = status_gui.render_kill_drill(entry)
        self.assertIn("does NOT count as field proof", text)


if __name__ == "__main__":
    unittest.main()
