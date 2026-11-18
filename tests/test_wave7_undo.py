"""Wave 7 Build 2 tests — UNDO completion (universal undo).

Proves the completion, not just the Perfection 1 producers:
settings and file writes still undo; sandbox files undo
through the sandbox boundary; extension transitions undo
through the shared harness (in-memory instance included);
irreversible classes are listed upfront and skipped, never
undone; a change with no restorer is reported and its
record preserved; the confirm-gated undo tool refuses
without a handler and writes nothing; the disclosure lists
the cannot-undo classes before any change is made.

Stdlib unittest, no GUI, no network. The confirm handler
is cleared in setUp/tearDown, so no test here ever undoes
through the real data/ tree. The undo store, the harness
store, and the sandbox root are all in a temp directory.
"""

import json
import os
import tempfile
import unittest

try:
    from _budget_isolation import restore_budget, snapshot_budget
except ImportError:  # run as a package module instead of top-level
    from tests._budget_isolation import (  # type: ignore
        restore_budget, snapshot_budget)

from core import confirm_gate
from core import extension_safety_harness as harness_mod
from core import failsafe as _failsafe
from core.resilience import resilience_gate as gate  # INTENTIONAL (Phase 3 R1): moved to core/resilience/
from core import sandbox_filesystem as sandbox_mod
from core.funnel import tools_gate as tools_gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core import undo_completion as undo7
from core import undo_producers_p1 as prod
from core import integration_harness_tools as w6
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/


def _result(payload):
    assert payload["status"] == "success", payload
    return json.loads(payload["result"])


class _Base(unittest.TestCase):
    def setUp(self):
        gate.reset_for_tests()
        prod.reset_producers_for_tests()
        try:
            _failsafe.reset()
        except Exception:  # noqa: BLE001
            pass
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        gate.configure_undo(os.path.join(self._tmp.name, "undo.json"))
        undo7.ensure_undo_completion_registered()
        confirm_gate.register_handler(None)
        self._confirm_gate = confirm_gate

    def tearDown(self):
        self._confirm_gate.register_handler(None)
        gate.reset_for_tests()
        prod.reset_producers_for_tests()
        w6.clear_test_harness_store()
        try:
            _failsafe.reset()
        except Exception:  # noqa: BLE001
            pass
        try:
            _failsafe._stop_flag_path = None
        except Exception:  # noqa: BLE001
            pass

    def _approve(self):
        self._confirm_gate.register_handler(lambda name, summary: True)


class TestDisclosure(_Base):
    def test_disclosure_lists_cannot_undo_before_any_change(self):
        disclosure = undo7.cannot_undo_disclosure()
        classes = [entry["class"] for entry in disclosure["cannot_undo"]]
        # Wave 3 list, intact.
        for name in ("message sent to another person",
                     "payment or purchase completed",
                     "message or post published externally",
                     "file deleted with no backup and no trash copy",
                     "system change that already restarted the machine"):
            self.assertIn(name, classes)
        # Wave 7 additions, with reasons.
        self.assertIn("extension revoked", classes)
        self.assertIn("mission journal entry appended", classes)
        for entry in disclosure["cannot_undo"]:
            self.assertTrue(entry["reason"])
        covered = [entry["class"] for entry in disclosure["covered_undoable"]]
        for name in ("setting changed", "file written",
                     "sandbox file written", "sandbox file deleted",
                     "extension state changed"):
            self.assertIn(name, covered)

    def test_disclosure_tool_is_read_only_and_classified(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in ("undo_cannot_list", "undo_status", "undo_last_change"):
            self.assertIn(name, names)
        self.assertIn("undo_cannot_list", tools_gate._READ_ONLY_TOOLS)
        self.assertIn("undo_status", tools_gate._READ_ONLY_TOOLS)
        self.assertIn("undo_last_change", tools_gate._CONFIRM_GATE_TOOLS)
        self.assertNotIn("undo_last_change", tools_gate._READ_ONLY_TOOLS)
        result = _result(tools.execute("undo_cannot_list", {}))
        classes = [entry["class"] for entry in result["cannot_undo"]]
        self.assertIn("extension revoked", classes)

    def test_status_is_read_only_and_honest_without_a_store(self):
        gate.reset_for_tests()
        result = _result(tools.execute("undo_status", {}))
        self.assertFalse(result["ok"])
        self.assertIn("no undo store", result["reason"])


class TestExistingProducersStillUndo(_Base):
    def test_setting_and_file_still_undo(self):
        store = {"sounds_enabled": False}
        prod.apply_setting_change(store, "sounds_enabled", True)
        self.assertTrue(gate.undo_last(approved=True)["undone"])
        self.assertFalse(store["sounds_enabled"])
        path = os.path.join(self._tmp.name, "note.txt")
        prod.write_file_with_undo(path, "after")
        self.assertTrue(gate.undo_last(approved=True)["undone"])
        self.assertFalse(os.path.exists(path))


class TestSandboxUndo(_Base):
    def _sandbox(self):
        root = os.path.join(self._tmp.name, "sandbox")
        os.makedirs(root)
        return sandbox_mod.FileSystemSandbox(root)

    def test_sandbox_write_undoes_to_prior_content(self):
        sandbox = self._sandbox()
        self.assertTrue(sandbox.write("a.txt", "before")["ok"])
        result = undo7.write_sandbox_file_with_undo(sandbox, "a.txt", "after")
        self.assertTrue(result["ok"])
        self.assertEqual(sandbox.read("a.txt")["content"], "after")
        undone = gate.undo_last(approved=True)
        self.assertTrue(undone["undone"], undone)
        self.assertEqual(undone["class"], "sandbox file written")
        self.assertEqual(sandbox.read("a.txt")["content"], "before")

    def test_sandbox_new_file_undoes_to_deletion(self):
        sandbox = self._sandbox()
        result = undo7.write_sandbox_file_with_undo(sandbox, "b.txt", "new")
        self.assertTrue(result["ok"])
        self.assertTrue(sandbox.read("b.txt")["ok"])
        self.assertTrue(gate.undo_last(approved=True)["undone"])
        self.assertFalse(sandbox.read("b.txt")["ok"])

    def test_sandbox_delete_undoes_to_prior_content(self):
        sandbox = self._sandbox()
        sandbox.write("c.txt", "keep me")
        result = undo7.delete_sandbox_file_with_undo(sandbox, "c.txt")
        self.assertTrue(result["ok"])
        self.assertFalse(sandbox.read("c.txt")["ok"])
        self.assertTrue(gate.undo_last(approved=True)["undone"])
        self.assertEqual(sandbox.read("c.txt")["content"], "keep me")

    def test_sandbox_run_tool_records_only_allowed_actions(self):
        self._approve()
        root = os.path.join(self._tmp.name, "runbox")
        os.makedirs(root)
        sandbox_mod.FileSystemSandbox(root).write("keep.txt", "keep")
        report = _result(tools.execute("sandbox_run", {
            "sandbox_dir": root,
            "actions_json": json.dumps([
                {"kind": "write", "path": "keep.txt", "content": "changed"},
                {"kind": "write", "path": "../evil.txt", "content": "x"},
                {"kind": "send", "path": "keep.txt", "content": "x"},
            ]),
        }))
        self.assertEqual(report["files_changed"], ["keep.txt"])
        status = undo7.undo_status()
        undoable = [entry for entry in status["records"]
                    if entry["can_be_taken_back"]]
        self.assertEqual(len(undoable), 1)
        self.assertEqual(undoable[0]["class"], "sandbox file written")
        self.assertTrue(gate.undo_last(approved=True)["undone"])
        self.assertEqual(
            sandbox_mod.FileSystemSandbox(root).read("keep.txt")["content"],
            "keep")

    def test_sandbox_restore_cannot_escape_the_sandbox(self):
        sandbox = self._sandbox()
        # A record whose root was replaced by a core path must
        # fail, not restore into core: the restorer resolves
        # through the sandbox constructor, which refuses core.
        gate.record_undo_change(
            "undo-escape", change_class="sandbox file written",
            before={"sandbox_root": os.path.join(
                os.path.dirname(sandbox_mod.__file__), "core"),
                "rel": "evil.txt", "content": "x"},
            after={"sandbox_root": "x", "rel": "evil.txt"})
        # The record is the latest undoable one only if no
        # restorer failure path intervenes; undo must fail
        # plainly and preserve the record.
        result = gate.undo_last(approved=True)
        self.assertFalse(result["undone"])
        summary = gate.shared_undo_store().summary()
        self.assertFalse(any(entry["taken_back"] for entry in summary))


class TestExtensionUndo(_Base):
    def _manifest(self, name="demo-ext"):
        return {"name": name, "version": "1.0.0",
                "licence": "MIT",
                "declared_permissions": ["files.own.read"],
                "declared_files": ["demo.txt"]}

    def test_enable_undoes_to_approved_through_shared_harness(self):
        store_path = os.path.join(self._tmp.name, "ext.json")
        w6.set_test_harness_store(store_path)
        harness = w6.shared_harness()
        self.assertTrue(harness.register(
            self._manifest(), directory=self._tmp.name)["ok"])
        prior = json.loads(json.dumps(harness.entries["demo-ext"]))
        self.assertTrue(harness.approve(
            "demo-ext", approved=True, human=True)["ok"])
        undo7.record_extension_transition(
            store_path, "demo-ext", prior, "approved")
        prior2 = json.loads(json.dumps(harness.entries["demo-ext"]))
        self.assertTrue(harness.enable("demo-ext")["ok"])
        undo7.record_extension_transition(
            store_path, "demo-ext", prior2, "enabled")
        self.assertEqual(harness.status("demo-ext")["state"], "enabled")
        undone = gate.undo_last(approved=True)
        self.assertTrue(undone["undone"], undone)
        # The shared in-memory instance, not just the file,
        # must show the restored state — otherwise the next
        # harness save would overwrite the undo.
        self.assertEqual(harness.status("demo-ext")["state"], "approved")
        undone = gate.undo_last(approved=True)
        self.assertTrue(undone["undone"], undone)
        self.assertEqual(harness.status("demo-ext")["state"], "registered")

    def test_revoke_is_skipped_and_listed_never_undone(self):
        store_path = os.path.join(self._tmp.name, "ext.json")
        w6.set_test_harness_store(store_path)
        harness = w6.shared_harness()
        harness.register(self._manifest(), directory=self._tmp.name)
        harness.revoke("demo-ext")
        undo7.note_irreversible_change(
            "undo-revoke", change_class="extension revoked")
        result = gate.undo_last(approved=True)
        self.assertFalse(result["undone"])
        skipped = result.get("skipped_non_undoable") or []
        self.assertTrue(any(entry["class"] == "extension revoked"
                            for entry in skipped))
        self.assertEqual(harness.status("demo-ext")["state"], "revoked")

    def test_extension_tools_record_transitions_and_undo(self):
        self._approve()
        store_path = os.path.join(self._tmp.name, "ext2.json")
        w6.set_test_harness_store(store_path)
        reg = _result(tools.execute("extension_register", {
            "manifest_json": json.dumps(self._manifest("tool-ext")),
            "directory": self._tmp.name}))
        self.assertTrue(reg["ok"], reg)
        self.assertTrue(_result(tools.execute(
            "extension_approve", {"name": "tool-ext"}))["ok"])
        self.assertTrue(_result(tools.execute(
            "extension_enable", {"name": "tool-ext"}))["ok"])
        self.assertEqual(
            w6.shared_harness().status("tool-ext")["state"], "enabled")
        undone = _result(tools.execute("undo_last_change", {}))
        self.assertTrue(undone["undone"], undone)
        self.assertEqual(
            w6.shared_harness().status("tool-ext")["state"], "approved")


class TestUndoGateBehaviour(_Base):
    def test_undo_without_restorer_reported_and_record_preserved(self):
        gate.record_undo_change(
            "undo-mystery", change_class="mystery class",
            before={"x": 1}, after={"x": 2})
        result = gate.undo_last(approved=True)
        self.assertFalse(result["undone"])
        self.assertIn("no restorer", result["reason"])
        summary = gate.shared_undo_store().summary()
        self.assertEqual(len(summary), 1)
        self.assertFalse(summary[0]["taken_back"])

    def test_irreversible_skipped_but_earlier_undoable_still_undone(self):
        store = {"key": "before"}
        prod.apply_setting_change(store, "key", "after")
        prod.note_irreversible(
            "undo-sent", change_class="message sent to another person")
        result = gate.undo_last(approved=True)
        self.assertTrue(result["undone"], result)
        self.assertEqual(store["key"], "before")
        skipped = result.get("skipped_non_undoable") or []
        self.assertTrue(any(
            entry["class"] == "message sent to another person"
            for entry in skipped))

    def test_undo_tool_refuses_without_handler_and_writes_nothing(self):
        store = {"key": "before"}
        prod.apply_setting_change(store, "key", "after")
        result = tools.execute("undo_last_change", {})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
        self.assertEqual(result["status"], "refused", result)
        self.assertEqual(result["kind"], "not_confirmed", result)
        self.assertEqual(store["key"], "after")

    def test_undo_tool_executes_with_handler(self):
        self._approve()
        store = {"key": "before"}
        prod.apply_setting_change(store, "key", "after")
        result = _result(tools.execute("undo_last_change", {}))
        self.assertTrue(result["undone"], result)
        self.assertEqual(store["key"], "before")

    def test_undo_status_tool_lists_records(self):
        store = {"key": "before"}
        prod.apply_setting_change(store, "key", "after")
        result = _result(tools.execute("undo_status", {}))
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["last_undoable"]["class"], "setting changed")

    def test_doctor_page_shows_cannot_undo_disclosure(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "gui", "doctor_page.py")
        with open(os.path.normpath(path), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("Cannot be taken back", source)
        self.assertIn("extension", source)
        self.assertIn("ensure_undo_completion_registered", source)


# Audit fix Batch 5 (TEST-02): L3 budget-isolation guest rule
# (shared helper tests/_budget_isolation.py) — this suite
# executes tools through the shared funnel, so it snapshots
# the budget window + clock + confirm handler, opens a clean
# window, and restores byte-for-byte, instead of inheriting
# the earlier suites' dispatches and tripping on ordering.
# The production 120-calls-per-60-seconds limit is untouched.
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
