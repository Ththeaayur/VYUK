"""Round-3 fix Q2 — the CDP outcome translators judge the act, not the
first-attach note.

_with_bootstrap (browser_session) prefixes "Attached to your Chrome
to do this. " when THIS call attached, so a landed navigation on the
call that attached reads "Attached to your Chrome to do this. Opened
'...'". The translators used to judge the raw string, so that
navigation read as cdp-navigate-failed while the page had actually
opened (section B, B10 runs 3-4) — and the same shape broke
click/type/press on a first-attach call, while a failed read on a
first-attach call read as a success. They now classify on the body
after the note (the _classify_attached_open precedent) and report
the full text.
"""
import unittest

from core.click_anywhere_tool import (
    _cdp_click_outcome,
    _cdp_navigate_outcome,
    _cdp_press_outcome,
    _cdp_read_outcome,
    _cdp_type_outcome,
)

NOTE = "Attached to your Chrome to do this. "
OPENED = "Opened 'Field Check Morning Page' \u2014 http://127.0.0.1:8937/morning.html."
LOADING = "Still loading http://127.0.0.1:8937/morning.html \u2014 the page didn't finish within 10s, but the navigation is under way and the connection is fine. Ask me to wait, or to read the page."
FAILED_OPEN = "Browser action failed: couldn't open http://127.0.0.1:9/nowhere (Page.goto: net::ERR_UNSAFE_PORT)."
REFUSED = "Refused: the URL scheme 'gopher' is not allowed for the attached browser."
NO_SESSION = "No controllable browser session is active. Tap 'Connect my browser' and I'll set it up \u2014 one tap, about a minute."
CLICKED = "Clicked 'Press Me Button'. The page text changed."
CLICK_MISS = "No control matched 'Press Me Button' on this page."
FILLED = "Filled 'hello'."
FILLED_ENTER = "Filled 'hello' and pressed Enter. Now on Field Check Morning Page."
FILLED_MISMATCH = "Filled 'hello' but it reads back as 'hallo', not the typed text \u2014 the fill did not land as typed."
PRESSED = "Pressed Enter. Now on Field Check Morning Page. (Key dispatched; whether it acted on the page is not verified.)"
PRESS_OTHER = "The key was not dispatched."
READ_FAILED = "Browser action failed: couldn't read the page (timeout)."
PAGE_TEXT = "Field Check Morning Page\n\nThe harbour code is HARBOUR-7Q3."


def _nav(result):
    return _cdp_navigate_outcome(result, action="navigate", target="http://x", by="url")


def _click(result):
    return _cdp_click_outcome(result, action="click", target="Press Me Button", by="text")


def _type(result):
    return _cdp_type_outcome(result, action="type", target="search", by="label")


def _press(result):
    return _cdp_press_outcome(result, action="press", target="Enter", by="key")


def _read(result):
    return _cdp_read_outcome(result, action="read", target="", by="")


class TestNavigateOutcome(unittest.TestCase):
    def test_first_attach_opened_succeeds(self):
        out = _nav(NOTE + OPENED)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("status"), "navigated")

    def test_plain_opened_succeeds(self):
        self.assertTrue(_nav(OPENED).get("ok"))

    def test_first_attach_loading_succeeds(self):
        self.assertTrue(_nav(NOTE + LOADING).get("ok"))

    def test_plain_loading_succeeds(self):
        self.assertTrue(_nav(LOADING).get("ok"))

    def test_failed_open_fails(self):
        out = _nav(FAILED_OPEN)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("rule"), "cdp-navigate-failed")

    def test_first_attach_failed_open_still_fails(self):
        out = _nav(NOTE + FAILED_OPEN)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("rule"), "cdp-navigate-failed")

    def test_refused_scheme_fails(self):
        out = _nav(REFUSED)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("rule"), "cdp-navigate-failed")

    def test_no_session_fails(self):
        out = _nav(NO_SESSION)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("rule"), "cdp-navigate-failed")

    def test_empty_fails(self):
        out = _nav("")
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("rule"), "cdp-navigate-failed")

    def test_report_keeps_the_full_text(self):
        out = _nav(NOTE + OPENED)
        self.assertTrue(out.get("report", "").startswith(NOTE))


class TestClickOutcome(unittest.TestCase):
    def test_first_attach_click_succeeds(self):
        out = _click(NOTE + CLICKED)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("status"), "clicked")

    def test_plain_click_succeeds(self):
        self.assertTrue(_click(CLICKED).get("ok"))

    def test_miss_fails(self):
        out = _click(CLICK_MISS)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("rule"), "cdp-click-failed")


class TestTypeOutcome(unittest.TestCase):
    def test_first_attach_fill_succeeds(self):
        out = _type(NOTE + FILLED)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("status"), "typed")

    def test_plain_fill_succeeds(self):
        self.assertTrue(_type(FILLED).get("ok"))

    def test_fill_with_enter_succeeds(self):
        self.assertTrue(_type(FILLED_ENTER).get("ok"))

    def test_readback_mismatch_fails(self):
        out = _type(FILLED_MISMATCH)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("rule"), "cdp-type-failed")

    def test_first_attach_mismatch_still_fails(self):
        out = _type(NOTE + FILLED_MISMATCH)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("rule"), "cdp-type-failed")


class TestPressOutcome(unittest.TestCase):
    def test_first_attach_press_succeeds(self):
        out = _press(NOTE + PRESSED)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("status"), "pressed")

    def test_plain_press_succeeds(self):
        self.assertTrue(_press(PRESSED).get("ok"))

    def test_other_fails(self):
        out = _press(PRESS_OTHER)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("rule"), "cdp-press-failed")


class TestReadOutcome(unittest.TestCase):
    def test_first_attach_failed_read_fails(self):
        out = _read(NOTE + READ_FAILED)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("rule"), "cdp-read-failed")

    def test_plain_failed_read_fails(self):
        out = _read(READ_FAILED)
        self.assertFalse(out.get("ok"))
        self.assertEqual(out.get("rule"), "cdp-read-failed")

    def test_first_attach_page_text_succeeds(self):
        self.assertTrue(_read(NOTE + PAGE_TEXT).get("ok"))

    def test_plain_page_text_succeeds(self):
        self.assertTrue(_read(PAGE_TEXT).get("ok"))

    def test_note_mid_text_is_not_stripped(self):
        # The note is a prefix only: page text that mentions it
        # mid-text is data, and the read still succeeds on it.
        text = "Notes\n\nAttached to your Chrome to do this. was in the draft."
        out = _read(text)
        self.assertTrue(out.get("ok"))
        self.assertIn("Attached to your Chrome", out.get("report", ""))


if __name__ == "__main__":
    unittest.main()
