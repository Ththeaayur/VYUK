"""§7 item 9 — application adapter #2: WINDOWS EXPLORER (D6 pick #2).

Proves the Windows Explorer adapter record against the item-9
framework: registration + the G2 terms gate (unread → read,
still unshippable without a verified version), signature
matching (incl. case-insensitive proc, both file-window
classes, desktop-shell exclusion, no-match and two-match
fallback), the 16-phrase vocabulary (exact match,
normalisation, unknown refusal), quirk-failure refusal,
confirm gating, wall refusal, version pinning, and receipts.

The adapter resolves only — no test here invokes, clicks,
types, or dispatches anything, and no test claims a real
Explorer version: the synthetic pin below is TEST-ONLY
("999.0-test-only") and must never be read as a Windows
build or an Explorer version. Real version pinning is field
work, and the adapter is honestly unshippable until it lands.

Every test resets the registry, so it cannot poison later
suites in the same process.
"""

import unittest

from core.desktop import app_adapters as aa  # INTENTIONAL (Phase 3 S7a-2): moved to core/desktop/
from core.desktop import explorer_adapter as ea  # INTENTIONAL (Phase 3 S7b): moved to core/desktop/

FG_EXPLORER = {"hwnd": 101, "pid": 4242, "title": "Documents",
               "proc": "explorer.exe", "window_class": "CabinetWClass"}
FG_EXPLORER_PANE = dict(FG_EXPLORER, window_class="ExplorerWClass")
FG_EXPLORER_UPPER_PROC = dict(FG_EXPLORER, proc="EXPLORER.EXE")
FG_DESKTOP = {"hwnd": 303, "pid": 4242, "title": "Desktop",
              "proc": "explorer.exe", "window_class": "Progman"}
FG_TASKBAR = {"hwnd": 304, "pid": 4242, "title": "Taskbar",
              "proc": "explorer.exe", "window_class": "Shell_TrayWnd"}
FG_OTHER = {"hwnd": 202, "pid": 4343, "title": "Other",
            "proc": "other.exe", "window_class": "OtherWindow"}

# The 15 core file-manager jobs, phrase → (kind, target).
EXPECTED_VOCABULARY = {
    "open folder": ("dialog", {"action": "folder", "folder": None,
                               "filename": None, "file_type": None}),
    "go up": ("action", {"name": "go up"}),
    "go back": ("action", {"name": "go back"}),
    "go forward": ("action", {"name": "go forward"}),
    "new folder": ("menu", {"path": ["New", "Folder"]}),
    "rename": ("menu", {"path": ["Rename"]}),
    "copy": ("menu", {"path": ["Copy"]}),
    "move": ("action", {"name": "move"}),
    "delete": ("menu", {"path": ["Delete"]}),
    "search in folder": ("action", {"name": "search in folder"}),
    "sort by name": ("action", {"name": "sort by name"}),
    "sort by date": ("action", {"name": "sort by date"}),
    "select all": ("action", {"name": "select all"}),
    "open file": ("action", {"name": "open file"}),
    "show properties": ("menu", {"path": ["Properties"]}),
    "open new window": ("action", {"name": "open new window"}),
}

# Phrases that resolve only with explicit confirmation.
CONFIRM_PHRASES = ("rename", "move", "delete")

# TEST-ONLY synthetic version pin. This is not a Windows build
# or an Explorer version and must never be presented as one;
# it exists only to prove the gate logic end to end (terms +
# a pin → shippable).
TEST_ONLY_VERSION = "999.0-test-only"


def _registered(with_terms=False, versions=None):
    record = ea.explorer_adapter_record()
    if versions is not None:
        record["verified_app_versions"] = list(versions)
    result = aa.register(record)
    assert result["ok"], result
    if with_terms:
        termed = aa.record_terms("explorer",
                                 terms=ea.EXPLORER_TERMS_EVIDENCE)
        assert termed["ok"], termed
    return record


class ExplorerAdapterTests(unittest.TestCase):
    def setUp(self):
        aa.reset_registry()

    def tearDown(self):
        aa.reset_registry()

    # -- registration + G2 terms gate --------------------------------

    def test_registration_starts_unread_and_unshippable(self):
        result = aa.register(ea.explorer_adapter_record())
        self.assertTrue(result["ok"])
        record = aa.get_adapter("explorer")
        self.assertEqual(record["terms_status"], aa.TERMS_UNREAD)
        self.assertEqual(record["adapter_version"], "1.0")
        self.assertEqual(record["verified_app_versions"], [])
        ship = aa.shippability("explorer")
        self.assertFalse(ship["shippable"])
        self.assertIn("terms unread", ship["reason"])

    def test_registration_refuses_asserted_terms_status(self):
        record = ea.explorer_adapter_record()
        record["terms_status"] = aa.TERMS_READ
        result = aa.register(record)
        self.assertFalse(result["ok"])
        self.assertIsNone(aa.get_adapter("explorer"))

    def test_terms_read_still_unshippable_without_version_pin(self):
        _registered(with_terms=True)
        record = aa.get_adapter("explorer")
        self.assertEqual(record["terms_status"], aa.TERMS_READ)
        self.assertIn("UI Automation",
                      record["terms_evidence"])
        ship = aa.shippability("explorer")
        self.assertFalse(ship["shippable"])
        self.assertIn("verified", ship["reason"])

    def test_test_only_version_pin_makes_shippable(self):
        # Gate logic end to end ONLY: the pin is synthetic and
        # proves nothing about any real Explorer version.
        _registered(with_terms=True, versions=[TEST_ONLY_VERSION])
        ship = aa.shippability("explorer")
        self.assertTrue(ship["shippable"])
        result = aa.resolve("new folder", foreground=FG_EXPLORER,
                            app_version=TEST_ONLY_VERSION)
        self.assertTrue(result["ok"])
        self.assertTrue(result["version_pinned"])

    def test_register_helper_records_terms(self):
        registered, termed = ea.register_explorer_adapter()
        self.assertTrue(registered["ok"])
        self.assertTrue(termed["ok"])
        self.assertEqual(aa.get_adapter("explorer")["terms_status"],
                         aa.TERMS_READ)
        self.assertFalse(aa.shippability("explorer")["shippable"])

    def test_wall_vocabulary_entry_refuses_registration(self):
        record = ea.explorer_adapter_record()
        record["vocabulary"]["show passwords"] = {
            "kind": "action", "name": "show-passwords"}
        record["safe_actions"] = list(record["safe_actions"]) + [
            "action:show-passwords"]
        result = aa.register(record)
        self.assertFalse(result["ok"])
        self.assertIsNone(aa.get_adapter("explorer"))

    def test_record_shape(self):
        record = ea.explorer_adapter_record()
        self.assertEqual(record["app_id"], "explorer")
        self.assertEqual(record["identity_signatures"],
                         [{"proc": "explorer.exe",
                           "window_class": "CabinetWClass"},
                          {"proc": "explorer.exe",
                           "window_class": "ExplorerWClass"}])
        self.assertEqual(len(record["vocabulary"]), 16)
        self.assertEqual(set(record["vocabulary"]),
                         set(EXPECTED_VOCABULARY))
        self.assertNotIn("password", " ".join(record["vocabulary"]))

    # -- signature matching -------------------------------------------

    def test_signature_match_case_insensitive_proc(self):
        _registered()
        result = aa.resolve("new folder",
                            foreground=FG_EXPLORER_UPPER_PROC)
        self.assertEqual(result["mode"], "adapter")
        self.assertEqual(result["adapter_id"], "explorer")

    def test_folder_pane_visible_class_matches(self):
        # ExplorerWClass is a file window too (folder pane
        # visible); the adapter claims it, not just CabinetWClass.
        _registered()
        result = aa.resolve("new folder",
                            foreground=FG_EXPLORER_PANE)
        self.assertEqual(result["mode"], "adapter")
        self.assertEqual(result["adapter_id"], "explorer")

    def test_desktop_shell_window_does_not_match(self):
        # Progman is the desktop shell, not a file window: the
        # adapter must not claim it even though the proc matches.
        _registered()
        result = aa.resolve("new folder", foreground=FG_DESKTOP)
        self.assertEqual(result["mode"], "generic")
        self.assertIn("no profile for this app", result["note"])

    def test_taskbar_window_does_not_match(self):
        _registered()
        result = aa.resolve("new folder", foreground=FG_TASKBAR)
        self.assertEqual(result["mode"], "generic")
        self.assertIn("no profile for this app", result["note"])

    def test_no_match_falls_back_to_generic(self):
        _registered()
        result = aa.resolve("new folder", foreground=FG_OTHER)
        self.assertEqual(result["mode"], "generic")
        self.assertIsNone(result["adapter_id"])
        self.assertIn("no profile for this app", result["note"])
        self.assertIn("Generic controls", aa.receipt_line(result))

    def test_two_match_is_generic_not_a_pick(self):
        _registered()
        other = ea.explorer_adapter_record()
        other["app_id"] = "explorer2"
        self.assertTrue(aa.register(other)["ok"])
        result = aa.resolve("new folder", foreground=FG_EXPLORER)
        self.assertEqual(result["mode"], "generic")
        self.assertIn("explorer", result["note"])
        self.assertIn("explorer2", result["note"])

    # -- vocabulary ----------------------------------------------------

    def test_all_fifteen_phrases_resolve_to_targets_not_execution(self):
        _registered()
        for phrase, (kind, target) in EXPECTED_VOCABULARY.items():
            # Confirm-gated phrases resolve confirmed here (the
            # stop-then-resolve behaviour is pinned separately).
            result = aa.resolve(phrase, foreground=FG_EXPLORER,
                                confirmed=(phrase in CONFIRM_PHRASES))
            self.assertTrue(result["ok"], phrase)
            self.assertEqual(result["mode"], "adapter", phrase)
            self.assertEqual(result["kind"], kind, phrase)
            self.assertEqual(result["target"], target, phrase)

    def test_vocabulary_normalisation_and_unknown_phrase(self):
        _registered()
        self.assertTrue(aa.resolve("  NEW FOLDER  ",
                                   foreground=FG_EXPLORER)["ok"])
        result = aa.resolve("compress this folder to zip",
                            foreground=FG_EXPLORER)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "unknown-phrase")
        self.assertIn("compress this folder to zip",
                      result["refusal"]["reason"])

    def test_permanent_delete_is_not_in_vocabulary(self):
        # Delete resolves to the Recycle Bin only; permanent
        # delete (Shift+Delete) is deliberately out of scope.
        _registered()
        for phrase in ("delete permanently",
                       "permanently delete this",
                       "empty recycle bin"):
            result = aa.resolve(phrase, foreground=FG_EXPLORER)
            self.assertFalse(result["ok"], phrase)
            self.assertEqual(result["refusal"]["code"],
                             "unknown-phrase", phrase)

    def test_delete_resolves_only_as_a_declared_action(self):
        # Resolution, not execution: the adapter names the action;
        # nothing here deletes anything.
        _registered()
        result = aa.resolve("delete", foreground=FG_EXPLORER,
                            confirmed=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "menu:Delete")

    # -- quirks ---------------------------------------------------------

    def test_quirk_failure_refuses_naming_quirk(self):
        record = ea.explorer_adapter_record()
        record["quirks"]["command_bar_layout"]["working"] = False
        self.assertTrue(aa.register(record)["ok"])
        result = aa.resolve("new folder", foreground=FG_EXPLORER)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "quirk-failed")
        self.assertIn("command_bar_layout", result["refusal"]["reason"])
        self.assertIsNone(result["target"])

    def test_rename_quirk_failure_refuses_rename(self):
        record = ea.explorer_adapter_record()
        record["quirks"]["rename_inline_edit"]["working"] = False
        self.assertTrue(aa.register(record)["ok"])
        result = aa.resolve("rename", foreground=FG_EXPLORER,
                            confirmed=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "quirk-failed")
        self.assertIn("rename_inline_edit", result["refusal"]["reason"])

    def test_recycle_bin_quirk_failure_refuses_delete(self):
        record = ea.explorer_adapter_record()
        record["quirks"]["recycle_bin_default"]["working"] = False
        self.assertTrue(aa.register(record)["ok"])
        result = aa.resolve("delete", foreground=FG_EXPLORER,
                            confirmed=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "quirk-failed")
        self.assertIn("recycle_bin_default",
                      result["refusal"]["reason"])

    def test_navigation_quirk_failure_refuses_go_up(self):
        record = ea.explorer_adapter_record()
        record["quirks"]["navigation_vs_view"]["working"] = False
        self.assertTrue(aa.register(record)["ok"])
        result = aa.resolve("go up", foreground=FG_EXPLORER)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "quirk-failed")
        self.assertIn("navigation_vs_view",
                      result["refusal"]["reason"])

    def test_working_quirks_resolve(self):
        _registered()
        self.assertTrue(aa.resolve("new folder",
                                   foreground=FG_EXPLORER)["ok"])
        self.assertTrue(aa.resolve("go up",
                                   foreground=FG_EXPLORER)["ok"])
        self.assertTrue(aa.resolve("delete",
                                   foreground=FG_EXPLORER,
                                   confirmed=True)["ok"])

    # -- safe / confirm --------------------------------------------------

    def test_confirm_phrases_stop_then_resolve_when_confirmed(self):
        _registered()
        for phrase in CONFIRM_PHRASES:
            stopped = aa.resolve(phrase, foreground=FG_EXPLORER)
            self.assertFalse(stopped["ok"], phrase)
            self.assertTrue(stopped["needs_confirmation"], phrase)
            self.assertEqual(stopped["refusal"]["code"],
                             "confirmation-required", phrase)
            resolved = aa.resolve(phrase, foreground=FG_EXPLORER,
                                  confirmed=True)
            self.assertTrue(resolved["ok"], phrase)

    def test_safe_actions_resolve_without_confirmation(self):
        _registered()
        for phrase in ("new folder", "copy", "go back",
                       "show properties", "open folder",
                       "open new window"):
            result = aa.resolve(phrase, foreground=FG_EXPLORER)
            self.assertTrue(result["ok"], phrase)
            self.assertFalse(result["needs_confirmation"], phrase)

    # -- version pinning + receipts ---------------------------------------

    def test_unverified_app_version_falls_back_to_generic(self):
        _registered(with_terms=True, versions=[TEST_ONLY_VERSION])
        result = aa.resolve("new folder", foreground=FG_EXPLORER,
                            app_version="0.0.0-unverified")
        self.assertEqual(result["mode"], "generic")
        self.assertIn("not verified", result["note"])
        self.assertIn("0.0.0-unverified", result["note"])

    def test_missing_app_version_is_honest_not_pinned(self):
        _registered()
        result = aa.resolve("new folder", foreground=FG_EXPLORER)
        self.assertTrue(result["ok"])
        self.assertFalse(result["version_pinned"])
        self.assertIn("not supplied", aa.receipt_line(result))

    def test_receipt_names_adapter_version_and_action(self):
        _registered(with_terms=True, versions=[TEST_ONLY_VERSION])
        result = aa.resolve("show properties",
                            foreground=FG_EXPLORER,
                            app_version=TEST_ONLY_VERSION)
        line = aa.receipt_line(result)
        self.assertIn("explorer", line)
        self.assertIn("v1.0", line)
        self.assertIn(TEST_ONLY_VERSION, line)
        self.assertIn("menu:Properties", line)
        fields = aa.receipt_fields(result)
        self.assertEqual(fields["adapter_id"], "explorer")
        self.assertEqual(fields["adapter_version"], "1.0")
        self.assertEqual(fields["app_version"], TEST_ONLY_VERSION)
        self.assertTrue(fields["version_pinned"])


if __name__ == "__main__":
    unittest.main()
