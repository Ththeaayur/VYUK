"""Staging tests for Wave 6 Build 1 — extension harness, gates first.

Run from the staging directory:  python3 test_extension_harness_w6.py
or:  python3 -m unittest test_extension_harness_w6 -v

No test invents a licence or a permission: every passing case uses
a licence string from the harness allow-list and a permission from
its known set; every failing case uses a string the harness itself
refuses. No test touches the working copy, the memory DB, the
vault, or .env — the harness store lives in a temp directory.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.extension_safety_harness import (  # noqa: E402
    ExtensionHarness, audit_licence, check_action, check_path_access,
    validate_manifest,
)


def clean_manifest(**overrides):
    manifest = {
        "name": "demo-notes",
        "version": "1.0.0",
        "licence": "MIT",
        "declared_permissions": ["files.own.read", "files.own.write"],
        "declared_files": ["notes.txt", "data/notes.json"],
    }
    manifest.update(overrides)
    return manifest


def enabled_harness():
    tmp = tempfile.TemporaryDirectory()
    harness = ExtensionHarness(os.path.join(tmp.name, "extensions.json"))
    result = harness.register(clean_manifest(), directory="/ext/demo-notes")
    assert result["ok"], result
    approved = harness.approve("demo-notes", approved=True, human=True,
                               reason="test approval")
    assert approved["ok"], approved
    enabled = harness.enable("demo-notes")
    assert enabled["ok"], enabled
    view = harness.extension_view("demo-notes")
    return tmp, harness, view


class TestManifestValidation(unittest.TestCase):
    def test_clean_manifest_passes(self):
        result = validate_manifest(clean_manifest())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["manifest"]["name"], "demo-notes")

    def test_missing_licence_fails_closed(self):
        manifest = clean_manifest()
        del manifest["licence"]
        self.assertFalse(validate_manifest(manifest)["ok"])

    def test_empty_licence_fails_closed(self):
        self.assertFalse(validate_manifest(clean_manifest(licence=""))["ok"])

    def test_unknown_licence_fails_closed(self):
        self.assertFalse(
            validate_manifest(clean_manifest(licence="SomeMadeUp-1.0"))["ok"])

    def test_blocked_licence_fails_closed(self):
        for licence in ("GPL-3.0", "LGPL-2.1", "AGPL-3.0", "MPL-2.0",
                        "CC-BY-NC-4.0", "MIT, non-commercial use only"):
            with self.subTest(licence=licence):
                self.assertFalse(
                    validate_manifest(clean_manifest(licence=licence))["ok"])
                ok, _ = audit_licence(licence)
                self.assertFalse(ok)

    def test_allowed_licences_pass_audit(self):
        for licence in ("MIT", "Apache-2.0", "BSD-3-Clause", "ISC",
                        "PSF-2.0", "CC0-1.0", "BSD-2-Clause"):
            with self.subTest(licence=licence):
                ok, _ = audit_licence(licence)
                self.assertTrue(ok)

    def test_unknown_permission_refused(self):
        result = validate_manifest(clean_manifest(
            declared_permissions=["files.own.read", "send_message"]))
        self.assertFalse(result["ok"])

    def test_missing_name_or_bad_version_refused(self):
        self.assertFalse(validate_manifest(clean_manifest(name=""))["ok"])
        self.assertFalse(validate_manifest(clean_manifest(version="1.0"))["ok"])
        self.assertFalse(validate_manifest(clean_manifest(version="v1"))["ok"])

    def test_traversal_and_absolute_declared_files_refused(self):
        self.assertFalse(validate_manifest(clean_manifest(
            declared_files=["../escape.txt"]))["ok"])
        self.assertFalse(validate_manifest(clean_manifest(
            declared_files=["/etc/passwd"]))["ok"])
        self.assertFalse(validate_manifest(clean_manifest(
            declared_files=[".env"]))["ok"])
        self.assertFalse(validate_manifest(clean_manifest(
            declared_files=["assistant.db"]))["ok"])


class TestSandboxPaths(unittest.TestCase):
    def test_own_directory_write_allowed_with_permission(self):
        _, _, view = enabled_harness()
        result = check_path_access(view, "/ext/demo-notes/notes.txt", "write")
        self.assertTrue(result["ok"], result)

    def test_out_of_directory_write_refused(self):
        _, _, view = enabled_harness()
        result = check_path_access(view, "/ext/other/notes.txt", "write")
        self.assertFalse(result["ok"])

    def test_traversal_write_refused(self):
        _, _, view = enabled_harness()
        result = check_path_access(view, "../other/notes.txt", "write")
        self.assertFalse(result["ok"])

    def test_protected_paths_refused_even_inside_directory(self):
        _, _, view = enabled_harness()
        for path in ("/ext/demo-notes/.env",
                     "/ext/demo-notes/assistant.db",
                     "/ext/demo-notes/key.pem"):
            with self.subTest(path=path):
                self.assertFalse(check_path_access(view, path, "read")["ok"])
                self.assertFalse(check_path_access(view, path, "write")["ok"])

    def test_undeclared_permission_refused(self):
        tmp = tempfile.TemporaryDirectory()
        harness = ExtensionHarness(os.path.join(tmp.name, "extensions.json"))
        harness.register(clean_manifest(declared_permissions=["files.own.read"]),
                         directory="/ext/demo-notes")
        harness.approve("demo-notes", approved=True, human=True)
        harness.enable("demo-notes")
        view = harness.extension_view("demo-notes")
        self.assertFalse(check_path_access(view, "/ext/demo-notes/x.txt", "write")["ok"])
        self.assertTrue(check_path_access(view, "/ext/demo-notes/x.txt", "read")["ok"])

    def test_no_directory_configured_refused(self):
        view = {"state": "enabled",
                "declared_permissions": ["files.own.write"], "directory": None}
        self.assertFalse(check_path_access(view, "x.txt", "write")["ok"])


class TestActionsAndApproval(unittest.TestCase):
    def test_send_without_approval_refused(self):
        _, _, view = enabled_harness()
        self.assertFalse(check_action(view, "send_message")["ok"])

    def test_send_with_denial_refused(self):
        _, _, view = enabled_harness()
        result = check_action(view, "send_message",
                              confirm=lambda action, reason: False)
        self.assertFalse(result["ok"])

    def test_send_with_approval_allowed_but_delete_still_needs_its_own(self):
        _, _, view = enabled_harness()
        result = check_action(view, "send_message",
                              confirm=lambda action, reason: True)
        self.assertTrue(result["ok"], result)
        # Approval is per-action: a send approval does not approve
        # a delete, and a None confirm refuses.
        self.assertFalse(check_action(view, "delete_file")["ok"])

    def test_delete_without_approval_refused(self):
        _, _, view = enabled_harness()
        self.assertFalse(check_action(view, "delete_file")["ok"])

    def test_purchase_refused_even_with_approval(self):
        _, _, view = enabled_harness()
        result = check_action(view, "purchase",
                              confirm=lambda action, reason: True)
        self.assertFalse(result["ok"])

    def test_memory_vault_env_credential_actions_always_refused(self):
        _, _, view = enabled_harness()
        for action in ("read_memory_db", "write_memory_db", "read_env",
                       "read_credentials", "read_vault", "write_vault",
                       "execute_code"):
            with self.subTest(action=action):
                result = check_action(view, action,
                                      confirm=lambda a, r: True)
                self.assertFalse(result["ok"])

    def test_unknown_action_refused(self):
        _, _, view = enabled_harness()
        self.assertFalse(check_action(view, "fly_to_the_moon")["ok"])

    def test_confirm_handler_raising_refuses(self):
        _, _, view = enabled_harness()

        def boom(action, reason):
            raise RuntimeError("handler blew up")

        self.assertFalse(check_action(view, "send_message", confirm=boom)["ok"])


class TestApprovalRecord(unittest.TestCase):
    def test_enable_requires_approval(self):
        tmp = tempfile.TemporaryDirectory()
        harness = ExtensionHarness(os.path.join(tmp.name, "extensions.json"))
        harness.register(clean_manifest(), directory="/ext/demo-notes")
        self.assertFalse(harness.enable("demo-notes")["ok"])
        self.assertTrue(harness.approve("demo-notes", approved=True,
                                        human=True)["ok"])
        self.assertTrue(harness.enable("demo-notes")["ok"])

    def test_approval_requires_human_and_approved(self):
        tmp = tempfile.TemporaryDirectory()
        harness = ExtensionHarness(os.path.join(tmp.name, "extensions.json"))
        harness.register(clean_manifest(), directory="/ext/demo-notes")
        self.assertFalse(harness.approve("demo-notes", approved=True,
                                         human=False)["ok"])
        self.assertFalse(harness.approve("demo-notes", approved=False,
                                         human=True)["ok"])
        self.assertEqual(harness.status("demo-notes")["state"], "registered")

    def test_revoked_extension_refused_everywhere(self):
        tmp, harness, view = enabled_harness()
        self.assertTrue(harness.revoke("demo-notes", reason="test revoke")["ok"])
        view = harness.extension_view("demo-notes")
        self.assertFalse(check_action(view, "read_own_file")["ok"])
        self.assertFalse(harness.enable("demo-notes")["ok"])
        self.assertFalse(harness.approve("demo-notes", approved=True,
                                         human=True)["ok"])
        self.assertFalse(harness.register(clean_manifest(),
                                          directory="/ext/demo-notes")["ok"])

    def test_approval_record_logged_with_reason(self):
        tmp = tempfile.TemporaryDirectory()
        harness = ExtensionHarness(os.path.join(tmp.name, "extensions.json"))
        harness.register(clean_manifest(), directory="/ext/demo-notes")
        harness.approve("demo-notes", approved=True, human=True,
                        reason="owner said yes in test")
        status = harness.status("demo-notes")
        events = [entry["event"] for entry in status["log"]]
        self.assertIn("registered", events)
        self.assertIn("approved", events)
        approved_entry = [e for e in status["log"]
                          if e["event"] == "approved"][0]
        self.assertEqual(approved_entry["reason"], "owner said yes in test")

    def test_corrupt_store_loads_empty_and_file_untouched(self):
        tmp = tempfile.TemporaryDirectory()
        store = os.path.join(tmp.name, "extensions.json")
        with open(store, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        harness = ExtensionHarness(store)
        self.assertEqual(harness.entries, {})
        self.assertIn("corrupt", harness.last_load_note)
        with open(store, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "{not json")

    def test_store_round_trips_and_never_touches_memory_db(self):
        tmp = tempfile.TemporaryDirectory()
        store = os.path.join(tmp.name, "extensions.json")
        harness = ExtensionHarness(store)
        harness.register(clean_manifest(), directory="/ext/demo-notes")
        harness.approve("demo-notes", approved=True, human=True)
        reloaded = ExtensionHarness(store)
        self.assertEqual(reloaded.status("demo-notes")["state"], "approved")
        self.assertFalse(os.path.exists(os.path.join(tmp.name, "assistant.db")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
