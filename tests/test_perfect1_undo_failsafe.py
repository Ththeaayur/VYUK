"""Code-Perfection Build 1 tests (stdlib unittest, no GUI, no network).

Wiring Build 3 left the Doctor undo button wired to a mechanism
with no producers, and no failsafe-reset action at all. This
suite tests the two producers and the reset that close those
gaps, Qt-free — the Doctor page itself cannot render in this
lab (no WebEngine, libEGL missing), so no render is claimed,
exactly as in the Wiring Build 3 tests. What IS tested is
everything the Doctor buttons drive:

- settings producer: a change is recorded before it is applied
  and undone to the previous value, or to deletion when the key
  did not exist; without an undo store, or with a value that
  cannot be stored in the JSON undo record, nothing changes;
- file producer: a write is undone to the previous content, a
  new file is undone to deletion, and an append is undone to
  the whole previous content; the loop's write_file tool records
  through the same hook, so a tool write is undoable too;
- irreversible actions: a sent message is recorded, skipped,
  and listed (never forced), and a lone irreversible record
  refuses plainly; a custom irreversible class is recorded
  non-undoable explicitly, with no restorer ever registered
  for it;
- failsafe reset: refused without confirmation, clears only
  the failsafe (a still-tripped wiring latch still pauses
  control, honestly reported), and re-arm plus reset together
  actually resume control; a stop flag still present makes
  the failsafe's own reset refuse, with its warning carried;
- source-level wiring: the Doctor page contains the Reset
  button and handler, the sounds toggle goes through the
  producer, and the write_file tool contains the record hook.

The real app failsafe is tripped only inside its own tests,
with reset() in a finally block, and every test resets the
shared gate and the producer registry in setUp/tearDown so no
latch or store leaks between tests. The stop-flag test uses a
temporary flag path only, never the tree's data/ directory.
"""

import os
import tempfile
import unittest

from core import failsafe as _failsafe
from core.resilience import resilience_gate as gate  # INTENTIONAL (Phase 3 R1): moved to core/resilience/
from core import undo_producers_p1 as prod


class _Base(unittest.TestCase):
    def setUp(self):
        gate.reset_for_tests()
        prod.reset_producers_for_tests()
        try:
            _failsafe.reset()
        except Exception:  # noqa: BLE001 — untripped already
            pass
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        gate.configure_undo(os.path.join(self._tmp.name, "undo.json"))
        prod.ensure_producers_registered()

    def tearDown(self):
        gate.reset_for_tests()
        prod.reset_producers_for_tests()
        try:
            _failsafe.reset()
        except Exception:  # noqa: BLE001
            pass
        # Restore the default stop-flag path (the stop-flag test
        # overrides it with a temporary path).
        _failsafe._stop_flag_path = None


class TestSettingsProducer(_Base):
    def test_change_records_before_applying_and_undoes_to_previous(self):
        store = {"sounds_enabled": False}
        result = prod.apply_setting_change(store, "sounds_enabled", True)
        self.assertTrue(result["ok"])
        self.assertTrue(store["sounds_enabled"])
        undone = gate.undo_last(approved=True)
        self.assertTrue(undone["undone"])
        self.assertEqual(undone["id"], result["change_id"])
        self.assertFalse(store["sounds_enabled"])

    def test_missing_key_undoes_to_deletion(self):
        store = {}
        result = prod.apply_setting_change(store, "new_key", 42)
        self.assertTrue(result["ok"])
        self.assertEqual(store["new_key"], 42)
        self.assertTrue(gate.undo_last(approved=True)["undone"])
        self.assertNotIn("new_key", store)

    def test_no_store_configured_changes_nothing(self):
        gate.reset_for_tests()
        prod.reset_producers_for_tests()
        store = {"sounds_enabled": False}
        result = prod.apply_setting_change(store, "sounds_enabled", True)
        self.assertFalse(result["ok"])
        self.assertIn("no undo store", result["reason"])
        self.assertFalse(store["sounds_enabled"])

    def test_non_jsonable_value_refuses_and_changes_nothing(self):
        store = {"key": "old"}
        result = prod.apply_setting_change(store, "key", object())
        self.assertFalse(result["ok"])
        self.assertEqual(store["key"], "old")

    def test_unapproved_undo_changes_nothing(self):
        store = {"key": "before"}
        prod.apply_setting_change(store, "key", "after")
        refused = gate.undo_last(approved=False)
        self.assertFalse(refused["undone"])
        self.assertEqual(store["key"], "after")


class TestFileProducer(_Base):
    def _path(self, name="note.txt"):
        return os.path.join(self._tmp.name, name)

    def test_write_undoes_to_previous_content(self):
        path = self._path()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("old content\n")
        result = prod.write_file_with_undo(path, "new content\n")
        self.assertTrue(result["ok"])
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "new content\n")
        self.assertTrue(gate.undo_last(approved=True)["undone"])
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "old content\n")

    def test_new_file_undoes_to_deletion(self):
        path = self._path()
        self.assertFalse(os.path.exists(path))
        result = prod.write_file_with_undo(path, "created\n")
        self.assertTrue(result["ok"])
        self.assertTrue(os.path.exists(path))
        self.assertTrue(gate.undo_last(approved=True)["undone"])
        self.assertFalse(os.path.exists(path))

    def test_append_undoes_to_whole_previous_content(self):
        path = self._path()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("ab")
        result = prod.write_file_with_undo(path, "cd", append=True)
        self.assertTrue(result["ok"])
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "abcd")
        self.assertTrue(gate.undo_last(approved=True)["undone"])
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "ab")

    def test_write_without_store_writes_nothing(self):
        gate.reset_for_tests()
        prod.reset_producers_for_tests()
        path = self._path()
        result = prod.write_file_with_undo(path, "nope\n")
        self.assertFalse(result["ok"])
        self.assertFalse(os.path.exists(path))

    def test_loop_tool_write_file_records_and_undoes(self):
        from core.funnel.tools_handlers_data_services import write_file_bytes

        path = self._path()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("before tool\n")
        write_file_bytes(path, "after tool\n")
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "after tool\n")
        self.assertTrue(gate.undo_last(approved=True)["undone"])
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "before tool\n")

    def test_loop_tool_write_without_store_still_writes(self):
        # The tool hook must never break the write: with no undo
        # store configured, it writes exactly as before, with no
        # undo claim.
        from core.funnel.tools_handlers_data_services import write_file_bytes

        gate.reset_for_tests()
        prod.reset_producers_for_tests()
        path = self._path()
        write_file_bytes(path, "plain write\n")
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "plain write\n")
        self.assertFalse(gate.undo_last(approved=True)["undone"])


class TestIrreversible(_Base):
    def test_sent_message_is_skipped_and_listed(self):
        store = {"key": "before"}
        prod.apply_setting_change(store, "key", "after")
        prod.note_irreversible("msg-1",
                               change_class="message sent to another person")
        result = gate.undo_last(approved=True)
        self.assertTrue(result["undone"])
        self.assertEqual(store["key"], "before")
        self.assertEqual([s["id"] for s in result["skipped_non_undoable"]],
                         ["msg-1"])
        # Now only the irreversible record remains: refuse plainly.
        again = gate.undo_last(approved=True)
        self.assertFalse(again["undone"])
        self.assertIn("cannot be taken back", again["reason"])

    def test_custom_irreversible_is_recorded_non_undoable(self):
        prod.note_irreversible("ext-1", change_class="external publish")
        summary = gate.shared_undo_store().summary()
        self.assertEqual(len(summary), 1)
        self.assertFalse(summary[0]["can_be_taken_back"])
        result = gate.undo_last(approved=True)
        self.assertFalse(result["undone"])


class TestFailsafeReset(_Base):
    def test_reset_refused_without_confirmation(self):
        _failsafe.trip_now("reset fence test")
        try:
            result = gate.reset_failsafe(approved=False)
            still_tripped = _failsafe.tripped()
        finally:
            _failsafe.reset()
        self.assertFalse(result["reset"])
        self.assertTrue(still_tripped)

    def test_reset_clears_failsafe_only_wiring_still_pauses(self):
        gate.trip_resilience("wiring trip")
        _failsafe.trip_now("failsafe trip")
        try:
            result = gate.reset_failsafe(approved=True, human=True)
            self.assertTrue(result["reset"])
            self.assertFalse(result["failsafe_still_tripped"])
            self.assertTrue(result["wiring_still_tripped"])
            self.assertFalse(result["armed"])
            self.assertIn("until re-arm", result["reason"])
            # Re-arm now, with the failsafe already cleared, resumes.
            rearmed = gate.rearm_resilience(approved=True, human=True)
            self.assertTrue(rearmed["rearmed"])
            self.assertFalse(rearmed["failsafe_still_tripped"])
            self.assertTrue(rearmed["armed"])
        finally:
            _failsafe.reset()
            gate.reset_for_tests()

    def test_rearm_then_reset_together_resume(self):
        gate.trip_resilience("wiring trip")
        _failsafe.trip_now("failsafe trip")
        try:
            rearmed = gate.rearm_resilience(approved=True, human=True)
            self.assertTrue(rearmed["rearmed"])
            self.assertTrue(rearmed["failsafe_still_tripped"])
            self.assertFalse(rearmed["armed"])
            result = gate.reset_failsafe(approved=True, human=True)
            self.assertTrue(result["reset"])
            self.assertFalse(result["wiring_still_tripped"])
            self.assertTrue(result["armed"])
            self.assertEqual(result["reason"], "")
        finally:
            _failsafe.reset()
            gate.reset_for_tests()

    def test_reset_with_stop_flag_present_refuses(self):
        flag = os.path.join(self._tmp.name, "nova_stop.flag")
        _failsafe.configure(stop_flag_path=flag)
        with open(flag, "w", encoding="utf-8") as handle:
            handle.write("")
        _failsafe.trip_now("flag trip")
        try:
            result = gate.reset_failsafe(approved=True, human=True)
            self.assertFalse(result["reset"])
            self.assertTrue(result["failsafe_still_tripped"])
            self.assertIn("stop flag", result["reason"])
            self.assertTrue(_failsafe.tripped())
            os.remove(flag)
            result = gate.reset_failsafe(approved=True, human=True)
            self.assertTrue(result["reset"])
            self.assertFalse(_failsafe.tripped())
        finally:
            _failsafe.reset()
            if os.path.exists(flag):
                os.remove(flag)


class TestSourceWiring(unittest.TestCase):
    def _source(self, relpath):
        path = os.path.join(os.path.dirname(__file__), "..", relpath)
        with open(os.path.normpath(path), encoding="utf-8") as handle:
            return handle.read()

    def test_doctor_page_contains_reset_button_and_handler(self):
        source = self._source("gui/doctor_page.py")
        for needle in (
            "btn_failsafe_reset",
            "_on_failsafe_reset",
            "Reset failsafe",
            "undo_producers_p1",
            "ensure_producers_registered",
            "reset_failsafe",
        ):
            self.assertIn(needle, source, needle)
        self.assertIn("HudDialog.confirm", source)
        self.assertNotIn("default=True", source)

    def test_sounds_toggle_goes_through_producer(self):
        # INTENTIONAL (refactor P1 step 5): the sounds toggle + store
        # moved verbatim to window_settings_voice.py. Assertions unchanged.
        source = self._source("gui/window_settings_voice.py")
        for needle in (
            "_SoundsEnabledStore",
            "apply_setting_change",
            "_on_sounds_toggled",
        ):
            self.assertIn(needle, source, needle)

    def test_tool_write_file_contains_record_hook(self):
        # INTENTIONAL (skills split B49): _tool_write_file moved to the
        # data_services mixin module; the needles live there now, not
        # in the facade — same assertion, superset.
        source = self._source("core/funnel/tools_handlers.py")  # Phase 3 F3 INTENTIONAL: live path check moved where bound
        source += self._source("core/funnel/tools_handlers_data_services.py")
        self.assertIn("undo_producers_p1", source)
        self.assertIn("record_file_write", source)


if __name__ == "__main__":
    unittest.main()
