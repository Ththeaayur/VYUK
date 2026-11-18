"""§7 item 9 — application adapter #3: VISUAL STUDIO CODE (D6 pick #3).

Proves the VS Code adapter record against the item-9 framework:
registration + the G2 terms gate (unread → read, still
unshippable without a verified version), signature matching
(incl. case-insensitive proc, proc-only signature, no-match
and two-match fallback, and the Chrome-collision proof: a
shared window class can never make chrome and vscode
ambiguous because the processes differ), the 19-phrase
vocabulary (exact match, normalisation, unknown refusal),
quirk-failure refusal, confirm gating, wall refusal, version
pinning, and receipts.

The adapter resolves only — no test here invokes, clicks,
types, or dispatches anything, and no test claims a real
VS Code version: the synthetic pin below is TEST-ONLY
("999.0-test-only") and must never be read as a VS Code
version. Real version pinning is field work, and the
adapter is honestly unshippable until it lands. The window
class is deliberately not in the signature (field-verification
owed — see the adapter module docstring); the collision tests
prove the proc-only signature still discriminates against
Chrome even under a shared class.

Every test resets the registry, so it cannot poison later
suites in the same process.
"""

import unittest

from core.desktop import app_adapters as aa  # INTENTIONAL (Phase 3 S7a-2): moved to core/desktop/
from core.desktop import chrome_adapter as ca  # INTENTIONAL (Phase 3 S7b): moved to core/desktop/
from core.desktop import vscode_adapter as va  # INTENTIONAL (Phase 3 S7b): moved to core/desktop/

FG_VSCODE = {"hwnd": 301, "pid": 5151,
             "title": "notes.txt - workspace - Visual Studio Code",
             "proc": "Code.exe", "window_class": "Chrome_WidgetWin_1"}
FG_VSCODE_UPPER_PROC = dict(FG_VSCODE, proc="CODE.EXE")
FG_VSCODE_OTHER_CLASS = dict(FG_VSCODE, window_class="OtherWindow")
FG_CHROME = {"hwnd": 101, "pid": 4242, "title": "Example — Google Chrome",
             "proc": "chrome.exe", "window_class": "Chrome_WidgetWin_1"}
FG_OTHER = {"hwnd": 202, "pid": 4343, "title": "Other",
            "proc": "other.exe", "window_class": "OtherWindow"}
FG_CLASS_ONLY = {"hwnd": 404, "pid": 4444, "title": "Shared class",
                 "proc": "other.exe", "window_class": "Chrome_WidgetWin_1"}

# The 19 core editor jobs, phrase → (kind, target).
EXPECTED_VOCABULARY = {
    "new file": ("menu", {"path": ["File", "New Text File"]}),
    "open file": ("menu", {"path": ["File", "Open File..."]}),
    "open folder": ("menu", {"path": ["File", "Open Folder..."]}),
    "save file": ("menu", {"path": ["File", "Save"]}),
    "save as": ("menu", {"path": ["File", "Save As..."]}),
    "close editor": ("menu", {"path": ["File", "Close Editor"]}),
    "close file": ("menu", {"path": ["File", "Close Editor"]}),
    "close without saving": ("action", {"name": "close without saving"}),
    "delete file": ("action", {"name": "delete file"}),
    "find in file": ("menu", {"path": ["Edit", "Find"]}),
    "replace in file": ("menu", {"path": ["Edit", "Replace"]}),
    "go to line": ("menu", {"path": ["Go", "Go to Line/Column..."]}),
    "toggle terminal": ("menu", {"path": ["View", "Terminal"]}),
    "run task": ("menu", {"path": ["Terminal", "Run Task..."]}),
    "run build task": ("menu", {"path": ["Terminal", "Run Build Task..."]}),
    "toggle sidebar": ("menu", {"path": ["View", "Primary Side Bar"]}),
    "split editor": ("menu", {"path": ["View", "Editor Layout",
                                        "Split Right"]}),
    "undo": ("menu", {"path": ["Edit", "Undo"]}),
    "redo": ("menu", {"path": ["Edit", "Redo"]}),
}

# Confirm-gated phrases (stop without confirmation, resolve with it).
CONFIRM_PHRASES = ("save as", "close without saving", "delete file",
                    "run task", "run build task")

# TEST-ONLY synthetic version pin. This is not a VS Code version
# and must never be presented as one; it exists only to prove
# the gate logic end to end (terms + a pin → shippable).
TEST_ONLY_VERSION = "999.0-test-only"


def _registered(with_terms=False, versions=None):
    record = va.vscode_adapter_record()
    if versions is not None:
        record["verified_app_versions"] = list(versions)
    result = aa.register(record)
    assert result["ok"], result
    if with_terms:
        termed = aa.record_terms("vscode", terms=va.VSCODE_TERMS_EVIDENCE)
        assert termed["ok"], termed
    return record


class VSCodeAdapterTests(unittest.TestCase):
    def setUp(self):
        aa.reset_registry()

    def tearDown(self):
        aa.reset_registry()

    # -- registration + G2 terms gate --------------------------------

    def test_registration_starts_unread_and_unshippable(self):
        result = aa.register(va.vscode_adapter_record())
        self.assertTrue(result["ok"])
        record = aa.get_adapter("vscode")
        self.assertEqual(record["terms_status"], aa.TERMS_UNREAD)
        self.assertEqual(record["adapter_version"], "1.0")
        self.assertEqual(record["verified_app_versions"], [])
        ship = aa.shippability("vscode")
        self.assertFalse(ship["shippable"])
        self.assertIn("terms unread", ship["reason"])

    def test_registration_refuses_asserted_terms_status(self):
        record = va.vscode_adapter_record()
        record["terms_status"] = aa.TERMS_READ
        result = aa.register(record)
        self.assertFalse(result["ok"])
        self.assertIsNone(aa.get_adapter("vscode"))

    def test_terms_read_still_unshippable_without_version_pin(self):
        _registered(with_terms=True)
        record = aa.get_adapter("vscode")
        self.assertEqual(record["terms_status"], aa.TERMS_READ)
        self.assertIn("Visual Studio Code",
                      record["terms_evidence"])
        self.assertIn("marketplace terms", record["terms_evidence"])
        ship = aa.shippability("vscode")
        self.assertFalse(ship["shippable"])
        self.assertIn("verified", ship["reason"])

    def test_test_only_version_pin_makes_shippable(self):
        # Gate logic end to end ONLY: the pin is synthetic and
        # proves nothing about any real VS Code version.
        _registered(with_terms=True, versions=[TEST_ONLY_VERSION])
        ship = aa.shippability("vscode")
        self.assertTrue(ship["shippable"])
        result = aa.resolve("save file", foreground=FG_VSCODE,
                            app_version=TEST_ONLY_VERSION)
        self.assertTrue(result["ok"])
        self.assertTrue(result["version_pinned"])

    def test_register_helper_records_terms(self):
        registered, termed = va.register_vscode_adapter()
        self.assertTrue(registered["ok"])
        self.assertTrue(termed["ok"])
        self.assertEqual(aa.get_adapter("vscode")["terms_status"],
                         aa.TERMS_READ)
        self.assertFalse(aa.shippability("vscode")["shippable"])

    def test_wall_vocabulary_entry_refuses_registration(self):
        record = va.vscode_adapter_record()
        record["vocabulary"]["show passwords"] = {
            "kind": "action", "name": "show-passwords"}
        record["safe_actions"] = list(record["safe_actions"]) + [
            "action:show-passwords"]
        result = aa.register(record)
        self.assertFalse(result["ok"])
        self.assertIsNone(aa.get_adapter("vscode"))

    def test_record_shape(self):
        record = va.vscode_adapter_record()
        self.assertEqual(record["app_id"], "vscode")
        # Proc-only by design: the window class is
        # field-verification owed and deliberately not named.
        self.assertEqual(record["identity_signatures"],
                         [{"proc": "Code.exe"}])
        self.assertEqual(len(record["vocabulary"]), 19)
        self.assertEqual(set(record["vocabulary"]),
                         set(EXPECTED_VOCABULARY))
        self.assertNotIn("password", " ".join(record["vocabulary"]))
        self.assertNotIn("palette", " ".join(record["vocabulary"]))

    # -- signature matching -------------------------------------------

    def test_signature_match_case_insensitive_proc(self):
        _registered()
        result = aa.resolve("save file", foreground=FG_VSCODE_UPPER_PROC)
        self.assertEqual(result["mode"], "adapter")
        self.assertEqual(result["adapter_id"], "vscode")

    def test_proc_only_signature_matches_any_window_class(self):
        # The signature names no class, so the class cannot
        # disqualify a Code.exe foreground — the proc is the
        # positive evidence.
        _registered()
        result = aa.resolve("save file",
                            foreground=FG_VSCODE_OTHER_CLASS)
        self.assertEqual(result["mode"], "adapter")
        self.assertEqual(result["adapter_id"], "vscode")

    def test_no_match_falls_back_to_generic(self):
        _registered()
        result = aa.resolve("save file", foreground=FG_OTHER)
        self.assertEqual(result["mode"], "generic")
        self.assertIsNone(result["adapter_id"])
        self.assertIn("no profile for this app", result["note"])
        self.assertIn("Generic controls", aa.receipt_line(result))

    def test_two_match_is_generic_not_a_pick(self):
        _registered()
        other = va.vscode_adapter_record()
        other["app_id"] = "vscode2"
        self.assertTrue(aa.register(other)["ok"])
        result = aa.resolve("save file", foreground=FG_VSCODE)
        self.assertEqual(result["mode"], "generic")
        self.assertIn("vscode", result["note"])
        self.assertIn("vscode2", result["note"])

    # -- Chrome collision proof ----------------------------------------

    def test_chrome_foreground_matches_chrome_not_vscode(self):
        _registered()
        self.assertTrue(aa.register(ca.chrome_adapter_record())["ok"])
        result = aa.resolve("new tab", foreground=FG_CHROME)
        self.assertEqual(result["mode"], "adapter")
        self.assertEqual(result["adapter_id"], "chrome")

    def test_vscode_foreground_with_chrome_class_matches_vscode(self):
        # The decisive collision case: even if VS Code's class
        # IS Chrome_WidgetWin_1, the process differs, so the
        # foreground matches vscode only — never ambiguous.
        _registered()
        self.assertTrue(aa.register(ca.chrome_adapter_record())["ok"])
        result = aa.resolve("save file", foreground=FG_VSCODE)
        self.assertEqual(result["mode"], "adapter")
        self.assertEqual(result["adapter_id"], "vscode")

    def test_shared_class_alone_matches_neither(self):
        # A Chrome_WidgetWin_1 window from another process is
        # neither Chrome nor VS Code: generic, never a pick.
        _registered()
        self.assertTrue(aa.register(ca.chrome_adapter_record())["ok"])
        result = aa.resolve("save file", foreground=FG_CLASS_ONLY)
        self.assertEqual(result["mode"], "generic")
        self.assertIsNone(result["adapter_id"])
        self.assertIn("no profile for this app", result["note"])

    # -- vocabulary ----------------------------------------------------

    def test_all_nineteen_phrases_resolve_to_targets_not_execution(self):
        _registered()
        for phrase, (kind, target) in EXPECTED_VOCABULARY.items():
            # Confirm-gated phrases resolve confirmed here (the
            # stop-then-resolve behaviour is pinned separately).
            result = aa.resolve(phrase, foreground=FG_VSCODE,
                                confirmed=(phrase in CONFIRM_PHRASES))
            self.assertTrue(result["ok"], phrase)
            self.assertEqual(result["mode"], "adapter", phrase)
            self.assertEqual(result["kind"], kind, phrase)
            self.assertEqual(result["target"], target, phrase)

    def test_vocabulary_normalisation_and_unknown_phrase(self):
        _registered()
        self.assertTrue(aa.resolve("  SAVE FILE  ",
                                   foreground=FG_VSCODE)["ok"])
        result = aa.resolve("open command palette",
                            foreground=FG_VSCODE)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "unknown-phrase")
        self.assertIn("open command palette",
                      result["refusal"]["reason"])

    def test_delete_file_resolves_only_as_a_declared_action(self):
        # Resolution, not execution: the adapter names the job;
        # nothing here deletes anything.
        _registered()
        result = aa.resolve("delete file", foreground=FG_VSCODE,
                            confirmed=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "action:delete file")

    # -- quirks ---------------------------------------------------------

    def test_quirk_failure_refuses_naming_quirk(self):
        record = va.vscode_adapter_record()
        record["quirks"]["dirty_file_indicator"]["working"] = False
        self.assertTrue(aa.register(record)["ok"])
        result = aa.resolve("close without saving",
                            foreground=FG_VSCODE)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "quirk-failed")
        self.assertIn("dirty_file_indicator", result["refusal"]["reason"])
        self.assertIsNone(result["target"])

    def test_terminal_focus_quirk_failure_refuses_toggle(self):
        record = va.vscode_adapter_record()
        record["quirks"]["terminal_focus"]["working"] = False
        self.assertTrue(aa.register(record)["ok"])
        result = aa.resolve("toggle terminal", foreground=FG_VSCODE)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "quirk-failed")
        self.assertIn("terminal_focus", result["refusal"]["reason"])

    def test_context_menu_quirk_failure_refuses_delete(self):
        record = va.vscode_adapter_record()
        record["quirks"]["explorer_context_menu"]["working"] = False
        self.assertTrue(aa.register(record)["ok"])
        result = aa.resolve("delete file", foreground=FG_VSCODE,
                            confirmed=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "quirk-failed")
        self.assertIn("explorer_context_menu",
                      result["refusal"]["reason"])

    def test_editor_layout_quirk_failure_refuses_split(self):
        record = va.vscode_adapter_record()
        record["quirks"]["editor_layout"]["working"] = False
        self.assertTrue(aa.register(record)["ok"])
        result = aa.resolve("split editor", foreground=FG_VSCODE)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "quirk-failed")
        self.assertIn("editor_layout", result["refusal"]["reason"])

    def test_working_quirks_resolve(self):
        _registered()
        self.assertTrue(aa.resolve("close without saving",
                                   foreground=FG_VSCODE,
                                   confirmed=True)["ok"])
        self.assertTrue(aa.resolve("toggle terminal",
                                   foreground=FG_VSCODE)["ok"])
        self.assertTrue(aa.resolve("split editor",
                                   foreground=FG_VSCODE)["ok"])

    # -- safe / confirm --------------------------------------------------

    def test_confirm_phrases_stop_then_resolve_when_confirmed(self):
        _registered()
        for phrase in CONFIRM_PHRASES:
            stopped = aa.resolve(phrase, foreground=FG_VSCODE)
            self.assertFalse(stopped["ok"], phrase)
            self.assertTrue(stopped["needs_confirmation"], phrase)
            self.assertEqual(stopped["refusal"]["code"],
                             "confirmation-required", phrase)
            resolved = aa.resolve(phrase, foreground=FG_VSCODE,
                                  confirmed=True)
            self.assertTrue(resolved["ok"], phrase)

    def test_safe_actions_resolve_without_confirmation(self):
        _registered()
        for phrase in ("new file", "save file", "open file",
                       "find in file", "go to line", "toggle sidebar",
                       "undo"):
            result = aa.resolve(phrase, foreground=FG_VSCODE)
            self.assertTrue(result["ok"], phrase)
            self.assertFalse(result["needs_confirmation"], phrase)

    # -- version pinning + receipts ---------------------------------------

    def test_unverified_app_version_falls_back_to_generic(self):
        _registered(with_terms=True, versions=[TEST_ONLY_VERSION])
        result = aa.resolve("save file", foreground=FG_VSCODE,
                            app_version="0.0.0-unverified")
        self.assertEqual(result["mode"], "generic")
        self.assertIn("not verified", result["note"])
        self.assertIn("0.0.0-unverified", result["note"])

    def test_missing_app_version_is_honest_not_pinned(self):
        _registered()
        result = aa.resolve("save file", foreground=FG_VSCODE)
        self.assertTrue(result["ok"])
        self.assertFalse(result["version_pinned"])
        self.assertIn("not supplied", aa.receipt_line(result))

    def test_receipt_names_adapter_version_and_action(self):
        _registered(with_terms=True, versions=[TEST_ONLY_VERSION])
        result = aa.resolve("save file", foreground=FG_VSCODE,
                            app_version=TEST_ONLY_VERSION)
        line = aa.receipt_line(result)
        self.assertIn("vscode", line)
        self.assertIn("v1.0", line)
        self.assertIn(TEST_ONLY_VERSION, line)
        self.assertIn("menu:File → Save", line)
        fields = aa.receipt_fields(result)
        self.assertEqual(fields["adapter_id"], "vscode")
        self.assertEqual(fields["adapter_version"], "1.0")
        self.assertEqual(fields["app_version"], TEST_ONLY_VERSION)
        self.assertTrue(fields["version_pinned"])


if __name__ == "__main__":
    unittest.main()
