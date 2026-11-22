"""UI rethink Build 3 (2026-11-16) — Settings + sidebar + status
strip declutter: search first, Top tasks, the explained card's
content in the search empty-state (43 -> 42 cards by the plan's
count; the tree measures 34 -> 33 sectionTitle cards), the
Always-allow entry, and the status strip as one docked dot + word.

Chat pass (2026-11-17): the sidebar is v6.3 again — section
labels back, the Build 3 Skills button gone (Skills is Ctrl+K-only
as in v6.3); the rest of Build 3 stands.

Window tests need Qt (the real MainWindow offscreen, via the
chat-ui WindowBase harness); the strip derivation is pure but
lives in a Qt module, so it is gated the same way.
"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    WindowBase = object  # type: ignore[assignment]
    _QT = False


def _blank_sample():
    return {"cpu": None, "mem": None, "disk": None, "net_bps": None,
            "battery_pct": None, "battery_plugged": None,
            "uptime_s": None, "procs": None}


@unittest.skipUnless(_QT, "Qt unavailable")
class TestStripDerivation(unittest.TestCase):
    """derive_strip_state: the word from real sources only, worst
    first. No counted "N errors" word exists — the tree has no
    app-wide error count to read, so none is invented."""

    def setUp(self):
        from gui.status_strip import derive_strip_state

        self._derive = derive_strip_state

    def test_ready_only_when_idle(self):
        # E-F102 (2026-11-20) supersedes the old pin below: a
        # disconnected session is not "Ready" — the pill read
        # Offline while the strip claimed all good. Offline is
        # amber (a degraded session, not a fault); Local core
        # shows as-is (the on-device brain is live) instead of
        # being swallowed into "Ready". "Ready" is honest only
        # when there is no session word at all.
        self.assertEqual(self._derive(_blank_sample(), "", False),
                         ("ok", "Ready"))
        self.assertEqual(self._derive(_blank_sample(), "Offline", False),
                         ("warning", "Offline"))
        self.assertEqual(self._derive(_blank_sample(), "Local core", False),
                         ("ok", "Local core"))

    def test_session_word_while_active(self):
        for word in ("Listening", "Responding", "Thinking",
                     "Connecting", "Reconnecting", "Setting up"):
            self.assertEqual(self._derive(_blank_sample(), word, False),
                             ("ok", word))

    def test_metric_warnings(self):
        sample = _blank_sample()
        sample["cpu"] = 90.0
        self.assertEqual(self._derive(sample, "", False),
                         ("warning", "CPU high"))
        sample = _blank_sample()
        sample["mem"] = 90.0
        self.assertEqual(self._derive(sample, "", False),
                         ("warning", "Memory high"))
        sample = _blank_sample()
        sample["disk"] = 90.0
        self.assertEqual(self._derive(sample, "", False),
                         ("warning", "Disk almost full"))
        sample = _blank_sample()
        sample["battery_pct"] = 20.0
        sample["battery_plugged"] = False
        self.assertEqual(self._derive(sample, "", False),
                         ("warning", "Battery low"))

    def test_metric_errors(self):
        # E-F104 (2026-11-20): an error must read as an error in
        # words, not just dot colour — "Disk almost full" /
        # "Battery low" are the warning words; the error words are
        # "Disk critically full" / "Battery critically low".
        sample = _blank_sample()
        sample["disk"] = 95.0
        self.assertEqual(self._derive(sample, "", False),
                         ("error", "Disk critically full"))
        sample = _blank_sample()
        sample["battery_pct"] = 10.0
        sample["battery_plugged"] = False
        self.assertEqual(self._derive(sample, "", False),
                         ("error", "Battery critically low"))

    def test_battery_plugged_never_warns(self):
        sample = _blank_sample()
        sample["battery_pct"] = 5.0
        sample["battery_plugged"] = True
        self.assertEqual(self._derive(sample, "", False),
                         ("ok", "Ready"))

    def test_unknown_sensors_never_warn(self):
        # Every sensor unknown: no warning, no error, just "Ready".
        self.assertEqual(self._derive(_blank_sample(), "", False),
                         ("ok", "Ready"))

    def test_mic_off_only_while_live(self):
        for word in ("Listening", "Responding", "Thinking"):
            self.assertEqual(self._derive(_blank_sample(), word, True),
                             ("warning", "Mic off"))
        # Muted at idle (or while connecting) is normal, not an error.
        for word in ("", "Offline", "Local core", "Connecting"):
            self.assertNotEqual(
                self._derive(_blank_sample(), word, True),
                ("warning", "Mic off"))

    def test_session_problem_and_setup_needed(self):
        self.assertEqual(self._derive(_blank_sample(), "Problem", False),
                         ("error", "Problem"))
        self.assertEqual(
            self._derive(_blank_sample(), "Setup needed", False),
            ("warning", "Setup needed"))

    def test_worst_first(self):
        sample = _blank_sample()
        sample["cpu"] = 95.0
        sample["disk"] = 96.0
        # E-F104: the winning metric error now reads
        # "Disk critically full", not the warning word.
        self.assertEqual(self._derive(sample, "", False),
                         ("error", "Disk critically full"))
        sample = _blank_sample()
        sample["cpu"] = 95.0
        self.assertEqual(self._derive(sample, "Listening", True),
                         ("warning", "CPU high"))


class TestStripDefaultOn(unittest.TestCase):
    """The docked strip is ON by default (presence/status_strip
    default True since Build 3) — the checkbox / tray / tool hide
    it; nobody has to opt in to see the window's bottom row."""

    def test_default_is_true(self):
        from core.config_spec import qsettings_get

        self.assertIs(qsettings_get("presence/status_strip"), True)


@unittest.skipUnless(_QT, "Qt unavailable")
class TestBuild3Window(WindowBase):
    def _show_settings(self):
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — boot may already be skipped
            pass
        self.window._show_page(4)
        self.app.processEvents()

    # ------------------------------------------------------------ sidebar
    def test_eleven_nav_buttons_in_v63_order_with_labels(self):
        # Chat pass (2026-11-17): the sidebar is v6.3 again — seven
        # buttons in v6.3 order, section labels back, the Build 3
        # Skills button gone (Skills is Ctrl+K-only, as in v6.3).
        # INTENTIONAL UPDATE (Stage 0 features batch 1, 2026-11-22):
        # eleven buttons — the four views (Calendar, Inbox,
        # Contacts, Calls) join before Settings under a new
        # VIEWS section label; the v6.3 seven keep their order
        # and Settings stays last. Skills is still Ctrl+K-only.
        labels = [b.text() for b in self.window.nav_buttons]
        self.assertEqual(labels, ["Home", "Activity", "Memory",
                                  "System Pulse", "Senses", "Doctor",
                                  "Calendar", "Inbox", "Contacts",
                                  "Calls", "Settings"])
        self.assertEqual(self.window.nav_buttons[-1].text(), "Settings")
        from PySide6.QtWidgets import QLabel

        # The buttons live in the sidebar's inner column: its labels
        # are the brand, the wake label, the four section labels and
        # the privacy card title.
        inner = self.window.nav_buttons[0].parentWidget()
        seen = {lbl.text() for lbl in inner.findChildren(QLabel)}
        for label in ("CORE", "MISSION", "ARCHIVE", "SYSTEM", "VIEWS"):
            self.assertIn(label, seen,
                          "a v6.3 section label is missing")
        self.assertNotIn("Skills", labels)

    def test_nav_accessible_descriptions_v63(self):
        expected = ["Show the Home page",
                    "Show the Activity page",
                    "Show the Memory page",
                    "Show the System Pulse page",
                    "Show the Senses page",
                    "Show the Doctor page",
                    "Show the Calendar page",
                    "Show the Inbox page",
                    "Show the Contacts page",
                    "Show the Calls page",
                    "Show the Settings page"]
        got = [b.accessibleDescription()
               for b in self.window.nav_buttons]
        self.assertEqual(got, expected)

    def test_skills_reachable_via_palette_not_sidebar(self):
        # Chat pass (2026-11-17): Skills has no sidebar button
        # (v6.3 shape) but stays one Ctrl+K palette row away.
        labels = [b.text() for b in self.window.nav_buttons]
        self.assertNotIn("Skills", labels)
        from gui.window_widgets_palette import CommandPalette

        palette = CommandPalette(self.window)
        try:
            commands = palette._build_commands()
            skills = [c for c in commands
                      if c["label"] == "Go to Skills"]
            self.assertEqual(len(skills), 1,
                             "the palette lost its Go to Skills row")
            skills[0]["action"]()
            self.app.processEvents()
            self.assertEqual(self.window.pages.currentIndex(), 3)
            self.assertFalse(
                any(b.isChecked() for b in self.window.nav_buttons))
            self.window.nav_buttons[0].click()
            self.app.processEvents()
            self.assertEqual(self.window.pages.currentIndex(), 0)
        finally:
            palette.close()

    def test_wake_toggle_still_at_the_bottom(self):
        self.assertIsNotNone(getattr(self.window, "btn_wake", None))
        self.assertEqual(self.window.btn_wake.text(),
                         "Off — click to enable")

    # ----------------------------------------------------------- settings
    def test_search_is_first_with_new_placeholder(self):
        self._show_settings()
        self.assertEqual(self.window.settings_search.placeholderText(),
                         "Type what you want to change")
        content_layout = self.window.settings_content.layout()
        idx_search = content_layout.indexOf(self.window.settings_search)
        header = self.window.settings_summary_label.parentWidget()
        idx_header = content_layout.indexOf(header)
        first_group = next(iter(
            self.window._settings_group_headers.values()))
        idx_group = content_layout.indexOf(first_group)
        self.assertLess(idx_search, idx_header,
                        "search must sit above the summary header")
        self.assertLess(idx_header, idx_group,
                        "summary header must sit above the groups")

    def test_card_count_35_explained_card_gone(self):
        # INTENTIONAL UPDATE (Stage 0 features batch 1, 2026-11-22):
        # 35, not 33. The count was already 35 before this batch —
        # the pin went stale because no neighbour run between
        # loophole batch 3 and stage0f1 included this suite
        # (proven: 35 on stage0f1_work/prescratch too). The two
        # additions are deliberate: +1 "Backup passphrase"
        # (loophole batch 3, #10 encrypted backup) and +1
        # "Feedback & crash reports" (loophole batch 7, #6).
        # Proven by title-literal count: 33 on
        # backup_release_v630_pre, 34 on backup_loophole_batch5_pre
        # through backup_loophole_batch7_pre, 35 on
        # backup_loophole_batch8_pre and the live tree; the live
        # window's sectionTitle labels list all 35.
        self._show_settings()
        self.assertEqual(len(self.window._settings_card_texts), 35)
        from PySide6.QtWidgets import QLabel

        titles = {lbl.text() for lbl in
                  self.window.settings_content.findChildren(
                      QLabel, "sectionTitle")}
        self.assertNotIn("What each setting does", titles,
                         "the explained card survives Build 3")
        self.assertIn("Remembered approvals", titles)

    def test_approvals_card_names_always_allow(self):
        self._show_settings()
        from PySide6.QtWidgets import QLabel

        for card in self.window._settings_card_texts:
            for lbl in card.findChildren(QLabel, "sectionTitle"):
                if lbl.text() == "Remembered approvals":
                    hay = " ".join(
                        w.text() for w in card.findChildren(QLabel))
                    self.assertIn("Always allow", hay)
                    return
        self.fail("Remembered approvals card not found")

    def test_search_no_match_shows_empty_state(self):
        self._show_settings()
        self.window.settings_search.setText("zzzznope")
        self.app.processEvents()
        self.assertTrue(self.window.settings_search_empty.isVisible())
        self.assertIn("No setting matches",
                      self.window.settings_search_empty_line.text())
        self.assertIn("clear the search",
                      self.window.settings_search_empty_next.text())
        for hdr in self.window._settings_group_headers:
            self.assertFalse(hdr.isVisible(),
                             f"group header {hdr.text()!r} survives a "
                             "no-match search")

    def test_search_matching_explanation_shows_row(self):
        self._show_settings()
        self.window.settings_search.setText("suggest things")
        self.app.processEvents()
        self.assertTrue(self.window.settings_search_empty.isVisible())
        rows = self.window.settings_search_empty_rows
        self.assertEqual(rows.count(), 1)
        from PySide6.QtWidgets import QLabel

        row = rows.itemAt(0).widget()
        titles = [lbl.text() for lbl in row.findChildren(QLabel)]
        self.assertIn("A choice about watching what you do", titles)
        self.assertIn("Clear the search to see every setting.",
                      self.window.settings_search_empty_next.text())

    def test_search_match_hides_empty_state(self):
        self._show_settings()
        self.window.settings_search.setText("microphone")
        self.app.processEvents()
        self.assertFalse(self.window.settings_search_empty.isVisible())
        from PySide6.QtWidgets import QLabel

        mic_visible = False
        for card in self.window._settings_card_texts:
            for lbl in card.findChildren(QLabel, "sectionTitle"):
                if lbl.text() == "Microphone" and card.isVisible():
                    mic_visible = True
        self.assertTrue(mic_visible, "Microphone card hides on its "
                                     "own search")

    def test_search_cleared_restores_page(self):
        self._show_settings()
        self.window.settings_search.setText("zzzznope")
        self.app.processEvents()
        self.assertTrue(self.window.settings_search_empty.isVisible())
        self.window.settings_search.setText("")
        self.app.processEvents()
        self.assertFalse(self.window.settings_search_empty.isVisible())
        for hdr in self.window._settings_group_headers:
            self.assertTrue(hdr.isVisible(),
                            f"group header {hdr.text()!r} stays hidden "
                            "after clearing the search")

    # -------------------------------------------------------------- strip
    def test_vitals_strip_is_docked_not_floating(self):
        strip = getattr(self.window, "vitals_strip", None)
        self.assertIsNotNone(strip, "the docked strip was not built")
        self.assertEqual(strip.height(), 32)
        self.assertIsNot(strip, getattr(self.window, "status_strip", None),
                         "the vitals strip still collides with the "
                         "Home header cluster")
        shell = getattr(self.window, "shell", None)
        if shell is not None:
            self.assertTrue(shell.isAncestorOf(strip),
                            "the strip is not docked inside the shell")

    def test_ensure_strip_returns_vitals_not_header(self):
        strip = self.window._ensure_status_strip()
        self.assertIs(strip, self.window.vitals_strip)

    def test_sink_status_returns_state_word_not_crash(self):
        # The pre-Build 3 shape crashed here: _ensure returned the
        # Home header cluster, which has no latest_text().
        self.window._set_status_strip_visible(True)
        self.app.processEvents()
        result = self.window._apply_status_strip_sink("status")
        self.assertTrue(result["ok"])
        self.assertTrue(result["visible"])
        self.assertEqual(result["text"],
                         self.window.vitals_strip.state_word())
        self.assertTrue(result["text"])
        result = self.window._apply_status_strip_sink("hide")
        self.assertTrue(result["ok"])
        self.assertFalse(result["visible"])
        self.assertEqual(result["text"], "")
        result = self.window._apply_status_strip_sink("show")
        self.assertTrue(result["ok"])
        self.assertTrue(result["visible"])


if __name__ == "__main__":
    unittest.main()
