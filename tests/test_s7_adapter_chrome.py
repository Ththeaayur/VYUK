"""§7 item 9 — application adapter #1: GOOGLE CHROME (D6 pick #1).

Proves the Chrome adapter record against the item-9 framework:
registration + the G2 terms gate (unread → read, still
unshippable without a verified version), signature matching
(incl. case-insensitive proc, exact window class, no-match and
two-match fallback), the 12-phrase vocabulary (exact match,
normalisation, unknown refusal), quirk-failure refusal,
confirm gating, wall refusal, version pinning, and receipts.

The adapter resolves only — no test here invokes, clicks,
types, or dispatches anything, and no test claims a real
Chrome version: the synthetic pin below is TEST-ONLY
("999.0-test-only") and must never be read as a Chrome
version. Real version pinning is field work, and the
adapter is honestly unshippable until it lands.

Every test resets the registry, so it cannot poison later
suites in the same process.
"""

import unittest

from core.desktop import app_adapters as aa  # INTENTIONAL (Phase 3 S7a-2): moved to core/desktop/
from core.desktop import chrome_adapter as ca  # INTENTIONAL (Phase 3 S7b): moved to core/desktop/

FG_CHROME = {"hwnd": 101, "pid": 4242, "title": "Example — Google Chrome",
             "proc": "chrome.exe", "window_class": "Chrome_WidgetWin_1"}
FG_CHROME_UPPER_PROC = dict(FG_CHROME, proc="CHROME.EXE")
FG_CHROME_FLYOUT = dict(FG_CHROME, window_class="Chrome_WidgetWin_2")
FG_OTHER = {"hwnd": 202, "pid": 4343, "title": "Other",
            "proc": "other.exe", "window_class": "OtherWindow"}

# The 12 core Chrome jobs, phrase → (kind, target).
EXPECTED_VOCABULARY = {
    "new tab": ("menu", {"path": ["New tab"]}),
    "new window": ("menu", {"path": ["New window"]}),
    "bookmark this page": (
        "menu", {"path": ["Bookmarks and lists", "Bookmark this tab"]}),
    "open history": ("menu", {"path": ["History", "History"]}),
    "open downloads": ("menu", {"path": ["Downloads"]}),
    "open settings": ("menu", {"path": ["Settings"]}),
    "go to address": ("action", {"name": "go to address"}),
    "reload": ("action", {"name": "reload"}),
    "go back": ("action", {"name": "go back"}),
    "go forward": ("action", {"name": "go forward"}),
    "find in page": ("action", {"name": "find in page"}),
    "close tab": ("action", {"name": "close tab"}),
}

# TEST-ONLY synthetic version pin. This is not a Chrome version
# and must never be presented as one; it exists only to prove
# the gate logic end to end (terms + a pin → shippable).
TEST_ONLY_VERSION = "999.0-test-only"


def _registered(with_terms=False, versions=None):
    record = ca.chrome_adapter_record()
    if versions is not None:
        record["verified_app_versions"] = list(versions)
    result = aa.register(record)
    assert result["ok"], result
    if with_terms:
        termed = aa.record_terms("chrome", terms=ca.CHROME_TERMS_EVIDENCE)
        assert termed["ok"], termed
    return record


class ChromeAdapterTests(unittest.TestCase):
    def setUp(self):
        aa.reset_registry()

    def tearDown(self):
        aa.reset_registry()

    # -- registration + G2 terms gate --------------------------------

    def test_registration_starts_unread_and_unshippable(self):
        result = aa.register(ca.chrome_adapter_record())
        self.assertTrue(result["ok"])
        record = aa.get_adapter("chrome")
        self.assertEqual(record["terms_status"], aa.TERMS_UNREAD)
        self.assertEqual(record["adapter_version"], "1.0")
        self.assertEqual(record["verified_app_versions"], [])
        ship = aa.shippability("chrome")
        self.assertFalse(ship["shippable"])
        self.assertIn("terms unread", ship["reason"])

    def test_registration_refuses_asserted_terms_status(self):
        record = ca.chrome_adapter_record()
        record["terms_status"] = aa.TERMS_READ
        result = aa.register(record)
        self.assertFalse(result["ok"])
        self.assertIsNone(aa.get_adapter("chrome"))

    def test_terms_read_still_unshippable_without_version_pin(self):
        _registered(with_terms=True)
        record = aa.get_adapter("chrome")
        self.assertEqual(record["terms_status"], aa.TERMS_READ)
        self.assertIn("Chrome DevTools Protocol",
                      record["terms_evidence"])
        ship = aa.shippability("chrome")
        self.assertFalse(ship["shippable"])
        self.assertIn("verified", ship["reason"])

    def test_test_only_version_pin_makes_shippable(self):
        # Gate logic end to end ONLY: the pin is synthetic and
        # proves nothing about any real Chrome version.
        _registered(with_terms=True, versions=[TEST_ONLY_VERSION])
        ship = aa.shippability("chrome")
        self.assertTrue(ship["shippable"])
        result = aa.resolve("new tab", foreground=FG_CHROME,
                            app_version=TEST_ONLY_VERSION)
        self.assertTrue(result["ok"])
        self.assertTrue(result["version_pinned"])

    def test_register_helper_records_terms(self):
        registered, termed = ca.register_chrome_adapter()
        self.assertTrue(registered["ok"])
        self.assertTrue(termed["ok"])
        self.assertEqual(aa.get_adapter("chrome")["terms_status"],
                         aa.TERMS_READ)
        self.assertFalse(aa.shippability("chrome")["shippable"])

    def test_wall_vocabulary_entry_refuses_registration(self):
        record = ca.chrome_adapter_record()
        record["vocabulary"]["show passwords"] = {
            "kind": "action", "name": "show-passwords"}
        record["safe_actions"] = list(record["safe_actions"]) + [
            "action:show-passwords"]
        result = aa.register(record)
        self.assertFalse(result["ok"])
        self.assertIsNone(aa.get_adapter("chrome"))

    def test_record_shape(self):
        record = ca.chrome_adapter_record()
        self.assertEqual(record["app_id"], "chrome")
        self.assertEqual(record["identity_signatures"],
                         [{"proc": "chrome.exe",
                           "window_class": "Chrome_WidgetWin_1"}])
        self.assertEqual(len(record["vocabulary"]), 12)
        self.assertEqual(set(record["vocabulary"]),
                         set(EXPECTED_VOCABULARY))
        self.assertNotIn("password", " ".join(record["vocabulary"]))

    # -- signature matching -------------------------------------------

    def test_signature_match_case_insensitive_proc(self):
        _registered()
        result = aa.resolve("new tab", foreground=FG_CHROME_UPPER_PROC)
        self.assertEqual(result["mode"], "adapter")
        self.assertEqual(result["adapter_id"], "chrome")

    def test_flyout_window_class_does_not_match_main_profile(self):
        # Chrome_WidgetWin_2 is a flyout window, not the main
        # browser window: the adapter must not claim it.
        _registered()
        result = aa.resolve("new tab", foreground=FG_CHROME_FLYOUT)
        self.assertEqual(result["mode"], "generic")
        self.assertIn("no profile for this app", result["note"])

    def test_no_match_falls_back_to_generic(self):
        _registered()
        result = aa.resolve("new tab", foreground=FG_OTHER)
        self.assertEqual(result["mode"], "generic")
        self.assertIsNone(result["adapter_id"])
        self.assertIn("no profile for this app", result["note"])
        self.assertIn("Generic controls", aa.receipt_line(result))

    def test_two_match_is_generic_not_a_pick(self):
        _registered()
        other = ca.chrome_adapter_record()
        other["app_id"] = "chrome2"
        self.assertTrue(aa.register(other)["ok"])
        result = aa.resolve("new tab", foreground=FG_CHROME)
        self.assertEqual(result["mode"], "generic")
        self.assertIn("chrome", result["note"])
        self.assertIn("chrome2", result["note"])

    # -- vocabulary ----------------------------------------------------

    def test_all_twelve_phrases_resolve_to_targets_not_execution(self):
        _registered()
        for phrase, (kind, target) in EXPECTED_VOCABULARY.items():
            # "close tab" is confirm-gated: resolve it confirmed here
            # (the stop-then-resolve behaviour is pinned separately).
            result = aa.resolve(phrase, foreground=FG_CHROME,
                                confirmed=(phrase == "close tab"))
            self.assertTrue(result["ok"], phrase)
            self.assertEqual(result["mode"], "adapter", phrase)
            self.assertEqual(result["kind"], kind, phrase)
            self.assertEqual(result["target"], target, phrase)

    def test_vocabulary_normalisation_and_unknown_phrase(self):
        _registered()
        self.assertTrue(aa.resolve("  NEW TAB  ",
                                   foreground=FG_CHROME)["ok"])
        result = aa.resolve("search google for cats",
                            foreground=FG_CHROME)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "unknown-phrase")
        self.assertIn("search google for cats",
                      result["refusal"]["reason"])

    def test_close_tab_resolves_only_as_a_declared_action(self):
        # Resolution, not execution: the adapter names the action;
        # nothing here closes anything.
        _registered()
        result = aa.resolve("close tab", foreground=FG_CHROME,
                            confirmed=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "action:close tab")

    # -- quirks ---------------------------------------------------------

    def test_quirk_failure_refuses_naming_quirk(self):
        record = ca.chrome_adapter_record()
        record["quirks"]["flyout_windows"]["working"] = False
        self.assertTrue(aa.register(record)["ok"])
        result = aa.resolve("new tab", foreground=FG_CHROME)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "quirk-failed")
        self.assertIn("flyout_windows", result["refusal"]["reason"])
        self.assertIsNone(result["target"])

    def test_address_bar_quirk_failure_refuses_go_to_address(self):
        record = ca.chrome_adapter_record()
        record["quirks"]["address_bar_focus"]["working"] = False
        self.assertTrue(aa.register(record)["ok"])
        result = aa.resolve("go to address", foreground=FG_CHROME)
        self.assertFalse(result["ok"])
        self.assertEqual(result["refusal"]["code"], "quirk-failed")
        self.assertIn("address_bar_focus", result["refusal"]["reason"])

    def test_working_quirks_resolve(self):
        _registered()
        self.assertTrue(aa.resolve("new tab",
                                   foreground=FG_CHROME)["ok"])
        self.assertTrue(aa.resolve("go to address",
                                   foreground=FG_CHROME)["ok"])

    # -- safe / confirm --------------------------------------------------

    def test_close_tab_stops_then_resolves_when_confirmed(self):
        _registered()
        stopped = aa.resolve("close tab", foreground=FG_CHROME)
        self.assertFalse(stopped["ok"])
        self.assertTrue(stopped["needs_confirmation"])
        self.assertEqual(stopped["refusal"]["code"],
                         "confirmation-required")
        resolved = aa.resolve("close tab", foreground=FG_CHROME,
                              confirmed=True)
        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["action"], "action:close tab")

    def test_safe_actions_resolve_without_confirmation(self):
        _registered()
        for phrase in ("new tab", "reload", "go back",
                       "bookmark this page", "open settings"):
            result = aa.resolve(phrase, foreground=FG_CHROME)
            self.assertTrue(result["ok"], phrase)
            self.assertFalse(result["needs_confirmation"], phrase)

    # -- version pinning + receipts ---------------------------------------

    def test_unverified_app_version_falls_back_to_generic(self):
        _registered(with_terms=True, versions=[TEST_ONLY_VERSION])
        result = aa.resolve("new tab", foreground=FG_CHROME,
                            app_version="0.0.0-unverified")
        self.assertEqual(result["mode"], "generic")
        self.assertIn("not verified", result["note"])
        self.assertIn("0.0.0-unverified", result["note"])

    def test_missing_app_version_is_honest_not_pinned(self):
        _registered()
        result = aa.resolve("new tab", foreground=FG_CHROME)
        self.assertTrue(result["ok"])
        self.assertFalse(result["version_pinned"])
        self.assertIn("not supplied", aa.receipt_line(result))

    def test_receipt_names_adapter_version_and_action(self):
        _registered(with_terms=True, versions=[TEST_ONLY_VERSION])
        result = aa.resolve("open downloads", foreground=FG_CHROME,
                            app_version=TEST_ONLY_VERSION)
        line = aa.receipt_line(result)
        self.assertIn("chrome", line)
        self.assertIn("v1.0", line)
        self.assertIn(TEST_ONLY_VERSION, line)
        self.assertIn("menu:Downloads", line)
        fields = aa.receipt_fields(result)
        self.assertEqual(fields["adapter_id"], "chrome")
        self.assertEqual(fields["adapter_version"], "1.0")
        self.assertEqual(fields["app_version"], TEST_ONLY_VERSION)
        self.assertTrue(fields["version_pinned"])


if __name__ == "__main__":
    unittest.main()
