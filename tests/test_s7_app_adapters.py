"""§7 item 9 — application-adapter FRAMEWORK ONLY (parent draft).

Proves the framework with mock apps: signature match/mismatch,
vocabulary resolution (menu + dialog), quirk-failure refusal,
generic fallback note, the G2 terms gate (unread terms cannot
ship), adapter-version pinning in receipts, and safe/confirm
enforcement. No real app is adapted here: the framework ships
ZERO app adapters, pending the owner's D6 pick of the first
three. Real app behaviour — real signatures, real vocabularies,
real terms reads — stays field work per adapter and is not
claimed here.

Every test resets the registry, so it cannot poison later
suites in the same process.
"""

import unittest

from core.desktop import app_adapters as aa  # INTENTIONAL (Phase 3 S7a-2): moved to core/desktop/


FG_NOTES = {"hwnd": 111, "pid": 42, "title": "Notes - draft",
            "proc": "notes.exe", "window_class": "NotesWindow"}
FG_OTHER = {"hwnd": 222, "pid": 43, "title": "Other",
            "proc": "other.exe", "window_class": "OtherWindow"}


def _adapter(app_id="notes", proc="notes.exe", versions=("1.0",),
             quirks=None, extra_vocab=None, safe=None, confirm=None):
    vocab = {
        "save": {"kind": "menu", "path": ["File", "Save"]},
        "open file": {"kind": "dialog", "action": "open",
                      "filename": "notes.txt"},
        "delete note": {"kind": "menu", "path": ["File", "Delete"]},
    }
    if extra_vocab:
        vocab.update(extra_vocab)
    record = {
        "app_id": app_id,
        "identity_signatures": [{"proc": proc}],
        "vocabulary": vocab,
        "quirks": quirks or {},
        "safe_actions": safe if safe is not None else
            ["menu:File → Save", "dialog:open"],
        "confirm_actions": confirm if confirm is not None else
            ["menu:File → Delete"],
        "adapter_version": "0.1",
        "verified_app_versions": list(versions),
    }
    return record


class AdapterFrameworkTests(unittest.TestCase):

    def setUp(self):
        aa.reset_registry()

    def tearDown(self):
        aa.reset_registry()

    # -- registration + terms gate -------------------------------------

    def test_registration_starts_unread_and_unshippable(self):
        result = aa.register(_adapter())
        self.assertTrue(result["ok"])
        record = aa.get_adapter("notes")
        self.assertEqual(record["terms_status"], aa.TERMS_UNREAD)
        ship = aa.shippability("notes")
        self.assertFalse(ship["shippable"])
        self.assertIn("terms unread", ship["reason"])

    def test_registration_refuses_asserted_terms_status(self):
        record = _adapter()
        record["terms_status"] = aa.TERMS_READ
        result = aa.register(record)
        self.assertFalse(result["ok"])
        self.assertIsNone(aa.get_adapter("notes"))
        self.assertIn("must start", result["refusal"]["reason"])

    def test_record_terms_without_evidence_refuses(self):
        aa.register(_adapter())
        result = aa.record_terms("notes")
        self.assertFalse(result["ok"])
        self.assertEqual(aa.get_adapter("notes")["terms_status"],
                         aa.TERMS_UNREAD)

    def test_record_terms_makes_shippable(self):
        aa.register(_adapter())
        result = aa.record_terms("notes", terms="vendor terms v1.0 read")
        self.assertTrue(result["ok"])
        ship = aa.shippability("notes")
        self.assertTrue(ship["shippable"])

    def test_not_applicable_needs_reason(self):
        aa.register(_adapter())
        self.assertFalse(aa.mark_terms_not_applicable("notes",
                                                      reason="")["ok"])
        self.assertTrue(aa.mark_terms_not_applicable(
            "notes",
            reason="no SDK depended on; terms do not restrict automation")["ok"])
        self.assertTrue(aa.shippability("notes")["shippable"])

    def test_unverified_versions_block_shipping(self):
        record = _adapter()
        record["verified_app_versions"] = []
        aa.register(record)
        aa.record_terms("notes", terms="vendor terms v1.0 read")
        ship = aa.shippability("notes")
        self.assertFalse(ship["shippable"])
        self.assertIn("verified", ship["reason"])

    def test_duplicate_and_validation_refusals_store_nothing(self):
        self.assertTrue(aa.register(_adapter())["ok"])
        self.assertFalse(aa.register(_adapter())["ok"])
        bad = _adapter(app_id="second")
        bad["safe_actions"] = ["menu:File → Save"]
        bad["confirm_actions"] = ["menu:File → Save"]
        self.assertFalse(aa.register(bad)["ok"])
        self.assertIsNone(aa.get_adapter("second"))
        wall = _adapter(app_id="third", extra_vocab={
            "fill password": {"kind": "action", "name": "fill-password"}})
        wall["safe_actions"] = ["menu:File → Save", "dialog:open",
                                "action:fill-password"]
        self.assertFalse(aa.register(wall)["ok"])
        self.assertIsNone(aa.get_adapter("third"))

    def test_undeclared_action_and_unknown_quirk_refused(self):
        record = _adapter()
        record["vocabulary"]["mystery"] = {"kind": "action",
                                           "name": "mystery-do"}
        self.assertFalse(aa.register(record)["ok"])
        record = _adapter()
        record["vocabulary"]["haunted"] = {"kind": "menu",
                                           "path": ["File", "Save"],
                                           "quirk": "ghost"}
        record["safe_actions"] = ["menu:File → Save", "dialog:open",
                                  "menu:File → Delete"]
        record["confirm_actions"] = []
        self.assertFalse(aa.register(record)["ok"])

    # -- signature matching ---------------------------------------------

    def test_signature_match_resolves_menu_and_names_receipt(self):
        aa.register(_adapter())
        result = aa.resolve("save", foreground=FG_NOTES,
                            app_version="1.0")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "adapter")
        self.assertEqual(result["adapter_id"], "notes")
        self.assertEqual(result["kind"], "menu")
        self.assertEqual(result["target"], {"path": ["File", "Save"]})
        self.assertTrue(result["version_pinned"])
        line = aa.receipt_line(result)
        self.assertIn("notes", line)
        self.assertIn("v0.1", line)
        self.assertIn("1.0", line)
        fields = aa.receipt_fields(result)
        self.assertEqual(fields["adapter_id"], "notes")
        self.assertEqual(fields["app_version"], "1.0")
        self.assertTrue(fields["version_pinned"])

    def test_signature_mismatch_disables_adapter_generic_fallback(self):
        aa.register(_adapter())
        result = aa.resolve("save", foreground=FG_OTHER,
                            app_version="1.0")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "generic")
        self.assertIsNone(result["adapter_id"])
        self.assertIn("no profile for this app",
                      result["note"])
        self.assertIn("Generic controls", aa.receipt_line(result))

    def test_ambiguous_match_is_generic_not_a_pick(self):
        aa.register(_adapter(app_id="notes", proc="shared.exe"))
        aa.register(_adapter(app_id="notes2", proc="shared.exe"))
        fg = dict(FG_NOTES, proc="shared.exe")
        result = aa.resolve("save", foreground=fg)
        self.assertEqual(result["mode"], "generic")
        self.assertIn("notes", result["note"])
        self.assertIn("notes2", result["note"])

    def test_title_substring_and_window_class_signatures(self):
        record = _adapter()
        record["identity_signatures"] = [
            {"proc": "notes.exe", "window_class": "NotesWindow",
             "title_substring": "Notes"}]
        aa.register(record)
        self.assertEqual(aa.resolve("save", foreground=FG_NOTES)["mode"],
                         "adapter")
        renamed = dict(FG_NOTES, title="Other title")
        self.assertEqual(aa.resolve("save", foreground=renamed)["mode"],
                         "generic")

    # -- vocabulary ------------------------------------------------------

    def test_vocabulary_normalisation_and_unknown_phrase(self):
        aa.register(_adapter())
        self.assertTrue(aa.resolve("  SAVE  ", foreground=FG_NOTES)["ok"])
        result = aa.resolve("save the world", foreground=FG_NOTES)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "unknown-phrase")
        self.assertIn("save the world", result["refusal"]["reason"])

    def test_dialog_vocabulary_resolves_to_spec_not_execution(self):
        aa.register(_adapter())
        result = aa.resolve("open file", foreground=FG_NOTES)
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "dialog")
        self.assertEqual(result["target"]["action"], "open")
        self.assertEqual(result["target"]["filename"], "notes.txt")

    # -- quirks ------------------------------------------------------------

    def test_quirk_failure_refuses_naming_quirk(self):
        quirks = {"slow-render": {"description": "canvas paints late",
                                  "working": False}}
        vocab = {"sketch": {"kind": "menu",
                            "path": ["Draw", "Sketch"],
                            "quirk": "slow-render"}}
        record = _adapter(quirks=quirks, extra_vocab=vocab,
                          safe=["menu:File → Save", "dialog:open",
                                "menu:Draw → Sketch"])
        record["confirm_actions"] = ["menu:File → Delete"]
        aa.register(record)
        result = aa.resolve("sketch", foreground=FG_NOTES)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "quirk-failed")
        self.assertIn("slow-render", result["refusal"]["reason"])
        # The refusal names the policy (no pixel workaround) AND
        # resolves nothing: a refusal that still returned a target
        # would be a workaround in disguise.
        self.assertIsNone(result["target"])

    def test_working_quirk_resolves(self):
        quirks = {"slow-render": {"description": "canvas paints late",
                                  "working": True}}
        vocab = {"sketch": {"kind": "menu",
                            "path": ["Draw", "Sketch"],
                            "quirk": "slow-render"}}
        record = _adapter(quirks=quirks, extra_vocab=vocab,
                          safe=["menu:File → Save", "dialog:open",
                                "menu:Draw → Sketch"])
        record["confirm_actions"] = ["menu:File → Delete"]
        aa.register(record)
        self.assertTrue(aa.resolve("sketch", foreground=FG_NOTES)["ok"])

    # -- safe / confirm ------------------------------------------------------

    def test_confirm_action_stops_then_resolves_when_confirmed(self):
        aa.register(_adapter())
        stopped = aa.resolve("delete note", foreground=FG_NOTES)
        self.assertFalse(stopped["ok"])
        self.assertTrue(stopped["needs_confirmation"])
        self.assertEqual(stopped["refusal"]["code"],
                         "confirmation-required")
        resolved = aa.resolve("delete note", foreground=FG_NOTES,
                              confirmed=True)
        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["action"], "menu:File → Delete")

    # -- version pinning ------------------------------------------------------

    def test_unverified_app_version_falls_back_to_generic(self):
        aa.register(_adapter())
        result = aa.resolve("save", foreground=FG_NOTES,
                            app_version="9.9")
        self.assertEqual(result["mode"], "generic")
        self.assertIn("not verified", result["note"])
        self.assertIn("9.9", result["note"])

    def test_missing_app_version_is_honest_not_pinned(self):
        aa.register(_adapter())
        result = aa.resolve("save", foreground=FG_NOTES)
        self.assertTrue(result["ok"])
        self.assertFalse(result["version_pinned"])
        self.assertIn("not supplied", aa.receipt_line(result))

    # -- registry hygiene ------------------------------------------------------

    def test_get_returns_copy_and_zero_adapters_ship_by_default(self):
        aa.register(_adapter())
        record = aa.get_adapter("notes")
        record["terms_status"] = aa.TERMS_READ
        self.assertEqual(aa.get_adapter("notes")["terms_status"],
                         aa.TERMS_UNREAD)
        self.assertEqual(aa.list_adapters(), ["notes"])
        self.assertTrue(aa.remove_adapter("notes"))
        self.assertEqual(aa.list_adapters(), [])


if __name__ == "__main__":
    unittest.main()
