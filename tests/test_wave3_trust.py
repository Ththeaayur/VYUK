"""Wave 3 trust / undo / rulings / distribution tests
(stdlib unittest, no GUI, no network).

Covers: scoped undo with the record as proof, cannot-undo
classes forced non-undoable, two distinct keys, C-14 rulings
(ask default, expiry, revocation, tripped refusal), explicit-
send-only crash reports, feed manifest structure, matrix
source discipline, guardian pre-flight, Track K vetoes,
tripwires detect-only, discriminators unknown-on-no-data,
and the R4-E degraded drill (which never greens M10).
"""

import os
import tempfile
import unittest

# Phase 4 D1 INTENTIONAL: staged rulings import removed with its deleted module (plan §4.1 D-3).
from core import distribution_hardening as dist
from core import trust_slices as trust
from core import undo_store as undo


class UndoTests(unittest.TestCase):
    def test_record_undo_verified(self):
        store = undo.UndoStore(os.path.join(tempfile.mkdtemp(), "u.json"))
        store.record("c1", change_class="file edited",
                     before="old", after="new")
        restored = []
        result = store.undo("c1", lambda before: restored.append(before) or True)
        self.assertTrue(result["undone"])
        self.assertEqual(restored, ["old"])
        self.assertTrue(store.summary()[0]["taken_back"])

    def test_cannot_undo_class_forced(self):
        store = undo.UndoStore(os.path.join(tempfile.mkdtemp(), "u.json"))
        entry = store.record("c2", change_class="message sent to another person",
                             before="draft", after="sent")
        self.assertFalse(entry["undoable"])
        self.assertFalse(store.undo("c2", lambda b: True)["undone"])

    def test_restorer_failure_is_not_undone(self):
        store = undo.UndoStore(os.path.join(tempfile.mkdtemp(), "u.json"))
        store.record("c3", change_class="file edited", before="a", after="b")
        self.assertFalse(store.undo("c3", lambda b: False)["undone"])

    def test_tripped_failsafe_refuses_undo(self):
        store = undo.UndoStore(os.path.join(tempfile.mkdtemp(), "u.json"))
        store.record("c4", change_class="file edited", before="a", after="b")
        result = store.undo("c4", lambda b: True,
                            failsafe_tripped=lambda: True)
        self.assertEqual(result["reason"], "failsafe-tripped")

    def test_two_keys_distinct_and_consumed_once(self):
        gate = undo.TwoKeyGate(expiry_s=60.0)
        self.assertFalse(gate.present("key-a")["open"])
        self.assertFalse(gate.present("key-a")["open"])
        self.assertTrue(gate.present("key-b")["open"])
        self.assertTrue(gate.consume()["opened"])
        self.assertFalse(gate.consume()["opened"])


# Phase 4 D1 INTENTIONAL: staged rulings test class removed with its deleted module (plan §4.1 D-3); no other test touched.
class DistributionTests(unittest.TestCase):
    def test_r4g_checklist_names_both_gates(self):
        checklist = dist.installer_validation_checklist()
        self.assertIn("sandbox_hyperv_availability", checklist["gated"])
        self.assertIn("zone_identifier_claim", checklist["gated"])
        self.assertIn("DEFERRED", checklist["signing"])

    def test_feed_manifest_structure_only(self):
        self.assertFalse(dist.validate_feed_manifest({"releases": [
            {"version": "1.0"}]})["ok"])
        good = dist.validate_feed_manifest({"releases": [
            dist.feed_entry_for("1.0", "https://example.invalid/x.zip",
                                b"bytes", "notes")]})
        self.assertTrue(good["ok"])

    def test_crash_report_never_sends_without_explicit_flag(self):
        reports = dist.CrashReports(tempfile.mkdtemp())
        path = reports.write_dump(error="boom", context={"api_key": "secret"})
        self.assertFalse(reports.send(path, explicit_send=False,
                                      transport=lambda p: None)["sent"])
        self.assertFalse(reports.send(path, explicit_send=True)["sent"])
        sent = []
        self.assertTrue(reports.send(path, explicit_send=True,
                                     transport=sent.append)["sent"])
        self.assertEqual(len(sent), 1)

    def test_matrix_lab_never_counts_as_owner(self):
        book = dist.MatrixBook(os.path.join(tempfile.mkdtemp(), "m.json"))
        book.record("row-13", "pass", source="lab")
        self.assertEqual(book.summary()["owner"], 0)
        self.assertEqual(book.owner_rows(), [])
        self.assertFalse(book.record("row-13", "pass", source="guess")["recorded"])


class TrustTests(unittest.TestCase):
    def test_guardian_unknown_blocks(self):
        result = trust.guardian_preflight(
            failsafe_ok=True, target_resolved=None, confirm_gate_ok=True,
            disk_ok=True, connectivity="ok")
        self.assertTrue(result["blocked"])
        self.assertIn("target_resolved", result["failing"])

    def test_track_k_vetoed_and_proven_needs_evidence(self):
        tk = trust.TrackK()
        self.assertFalse(tk.set("face_avatar", "pilot")["ok"])
        self.assertFalse(tk.set("guardian_angel", "proven")["ok"])
        self.assertTrue(tk.set("guardian_angel", "proven",
                               evidence="w3-test")["ok"])

    def test_tripwire_detect_only(self):
        tw = trust.Tripwires()
        tw.register("suspicious", "rm -rf /")
        result = tw.scan("user typed rm -rf / by accident")
        self.assertEqual(result["count"], 1)
        self.assertIn("never replace", result["note"])

    def test_self_attack_read_only_findings(self):
        result = trust.self_attack_at_idle(
            {"failsafe": lambda: (True, "healthy"),
             "broken": lambda: (False, "needs attention")})
        self.assertEqual(result["failed"], ["broken"])

    def test_discriminators_unknown_on_no_data(self):
        verdict = trust.discriminate()
        self.assertEqual(verdict["crash_vs_sleep"], "unknown")
        self.assertEqual(verdict["connectivity"], "unknown")

    def test_degraded_drill_never_greens_m10(self):
        drill = trust.degraded_mode_drill(offline=True, failsafe_tripped=False,
                                          disk_full=False)
        self.assertTrue(drill["degraded"])
        self.assertIn("offline", drill["triggers"])
        self.assertIn("NOT greened", drill["m10"])

    def test_crash_consistent_refuses_assistant_db(self):
        result = trust.crash_consistent_check("/tmp/assistant.db")
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
