"""B7F3: the "?" beside the Scheduled headline is a circled
button (Build 7 Finding 3).

The finding: the "?" is the designed Build 2 help button (it
opens the Scheduled explainer), but it carried the borderless
"activityTextAction" chrome — transparent, no border — so beside
the headline it read as a stray mark, not a button. The ruling
is BUTTON CHROME only: the "?" becomes a circled "?" button
(round outline, the standard help affordance). One widget; no
copy change; it must keep opening the same help.

The fix gives the help button its own objectName
("activityHelpButton") with a circled rule in the house sheet.
"activityTextAction" is NOT restyled: it is shared by the
Details / pending / Refresh / journal buttons, which stay quiet
text. The only existing test that names the "?" button's
objectName (test_scifi_activity.test_scheduled_help_button)
moves to the new name with a B7F3 comment — the rename IS the
fix.

Qt only: the whole suite needs widgets, so without PySide6 every
class skips (the test_chat_ui / test_rethink_build6 shape).
"""

import time
import unittest
from unittest import mock

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PySide6  # noqa: F401 — availability probe only
    from PySide6.QtWidgets import QPushButton
    from tests.test_b7f2_sched_order import _seed_scheduled
    from tests.test_chat_ui import WindowBase
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    # TestCase, not object: the loader only collects TestCase
    # subclasses, so an object base would run ZERO tests under
    # sys (rc 5, "NO TESTS RAN") instead of 5 skipped — the
    # test_rethink_build6 shape, where the mixin is a TestCase.
    WindowBase = unittest.TestCase  # type: ignore[assignment]

HELP_TEXT = ("VYUK will speak these out loud when "
             "they fire. They're saved on this device, "
             "so they survive a restart.")


def _pump(app, seconds=0.6):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.05)
    app.processEvents()


@unittest.skipUnless(_QT, "Qt unavailable")
class HelpButtonTests(WindowBase):
    """The real MainWindow offscreen (WindowBase), the Scheduled
    tab seeded the way the finding measured it."""

    def setUp(self):
        super().setUp()
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — boot skip is best-effort
            pass
        _seed_scheduled()
        self.window._show_page(1)
        self.app.processEvents()
        self.window._refresh_activity_tabs()
        self.window._activity_tabs.setCurrentIndex(1)
        _pump(self.app, 0.8)

    def _help_button(self):
        """The single "?" in the Scheduled tab."""
        tab = self.window._activity_tabs.widget(1)
        helps = [b for b in tab.findChildren(QPushButton)
                 if b.text() == "?"]
        self.assertEqual(len(helps), 1,
                         f"expected exactly one '?' button, got "
                         f"{len(helps)}")
        return helps[0]

    def test_help_button_is_circled(self):
        # The fix: its own objectName with a circled rule in the
        # house sheet — an 18 px circle (9 px radius, 1 px rim),
        # not the shared borderless text-action chrome.
        btn = self._help_button()
        self.assertEqual(btn.objectName(), "activityHelpButton")
        from gui.style_sheet import PREMIUM_STYLE
        self.assertIn("QPushButton#activityHelpButton", PREMIUM_STYLE)
        self.assertIn("border-radius: 9px", PREMIUM_STYLE)
        self.assertIn("border: 1px solid", PREMIUM_STYLE)
        # The rule must actually land: the rendered button is the
        # 18x18 circle the sheet pins (16 px content + the 1 px
        # rim each side — QSS width is the content box, so
        # min/max 16 lands a total width of 18).
        self.window.resize(1280, 800)
        _pump(self.app, 0.8)
        self.assertEqual(btn.width(), 18,
                         f"help button width is not 18: {btn.width()}")
        self.assertEqual(btn.height(), 18,
                         f"help button height is not 18: "
                         f"{btn.height()}")

    def test_help_button_opens_the_same_help(self):
        # The chrome changes; the behaviour does not: the click
        # opens the same explainer with the same title and text.
        btn = self._help_button()
        import gui.design_system.dialogs as dialogs
        with mock.patch.object(
                dialogs.HudDialog, "information") as info:
            btn.click()
            self.app.processEvents()
        self.assertEqual(info.call_count, 1)
        self.assertEqual(info.call_args[0][1], "Scheduled reminders")
        self.assertEqual(info.call_args[0][2], HELP_TEXT)

    def test_help_button_text_and_tooltip_unchanged(self):
        # No copy change: still "?", still "About this section".
        btn = self._help_button()
        self.assertEqual(btn.text(), "?")
        self.assertEqual(btn.toolTip(), "About this section")
        self.assertTrue(btn.isEnabled())

    def test_other_text_actions_untouched(self):
        # activityTextAction stays the borderless quiet-text
        # chrome for every other button that uses it — the fix
        # gives the "?" its own name instead of restyling the
        # shared one. The Scheduled tab's Refresh is the witness.
        from gui.style_sheet import PREMIUM_STYLE
        self.assertIn(
            "QPushButton#activityTextAction { background: "
            "transparent; border: none;",
            PREMIUM_STYLE)
        tab = self.window._activity_tabs.widget(1)
        refresh = [b for b in tab.findChildren(QPushButton)
                   if b.text() == "Refresh"]
        self.assertEqual(len(refresh), 1)
        self.assertEqual(refresh[0].objectName(),
                         "activityTextAction")

    def test_help_button_visible_at_both_sizes(self):
        # The headline sits outside the scroll area, so the
        # circled "?" must be visible at both window sizes, and
        # the headline counts must be unchanged.
        for width, height in ((1280, 800), (1024, 768)):
            self.window.resize(width, height)
            _pump(self.app, 0.8)
            btn = self._help_button()
            self.assertTrue(btn.isVisible(),
                            f"'?' not visible at {width}x{height}")
            self.assertTrue(btn.isEnabled())
            self.assertEqual(
                self.window._activity_headlines["scheduled"]
                ["line"].text(),
                "2 due today · next 09:00 · 1 already spoken")
