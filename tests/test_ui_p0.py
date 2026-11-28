"""UI Upgrade Programme Phase 0 — design foundation (2026-11-23).

Covers: kicker headers on all 10 _page_shell callers + the Home
wordmark, HudSwitch adoption (41 toggles), HudTabs on Activity,
EmptyState on Memory / Activity / Reminders, ContextDrawer on the
Memory web detail panel, StatusPill on the Calls outcome cell, the
HudToast helper + web-forget confirmation, the 3 JARVIS lines gone,
the set_theme seams, and the HELD decisions (Inbox drawer, Doctor
pills, MissionRail/ActionTrace, TelemetryPanel, SegmentBar,
CommandRadar, HudBackdrop, OrbThumbnail). Source tests run on both
interpreters; window tests need Qt (skipped without it).
"""

from __future__ import annotations

import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from tests.test_chat_ui import WindowBase  # noqa: E402


def _src(rel):
    return (ROOT / rel).read_text()


class UiP0SourceTests(unittest.TestCase):
    """Source-level Phase 0 assertions — run on both interpreters."""

    def test_shell_default_is_kicker(self):
        src = _src("gui/window_pages_activity.py")
        self.assertIn("kicker: bool = True", src)
        self.assertNotIn("kicker=False", src)

    def test_all_ten_callers_pass_kicker(self):
        import re
        for rel in ("gui/window_pages_memory.py",
                    "gui/window_pages_system.py",
                    "gui/window_pages_activity.py",
                    "gui/window_settings.py",
                    "gui/window_pages_views.py"):
            src = _src(rel)
            calls = re.findall(r"_page_shell\((?:[^()]|\([^()]*\))*?\)",
                               src, re.S)
            for call in calls:
                if "bool" in call:
                    continue  # the def itself, not a caller
                self.assertIn("kicker=True", call,
                              f"{rel}: a _page_shell caller lacks kicker=True")

    def test_jarvis_three_lines_gone(self):
        self.assertNotIn("exactly like JARVIS",
                         _src("gui/visualizer_glass.py"))
        self.assertNotIn("JARVIS-like", _src("gui/style_tokens.py"))
        self.assertNotIn("J.A.R.V.I.S./Iron Man/Stark",
                         _src("gui/orb_sphere.py"))

    def test_no_qcheckbox_in_settings_or_pages(self):
        for rel in ("gui/window_settings_groups.py",
                    "gui/window_settings_voice.py",
                    "gui/window_settings_advbrain.py",
                    "gui/window_settings_advcards.py",
                    "gui/window_memory_learned.py",
                    "gui/window_pages_activity.py"):
            self.assertNotIn("QCheckBox(", _src(rel), rel)

    def test_held_notes_present(self):
        for rel, needle in (
                ("gui/design_system/mission.py", "Phase 0 HELD"),
                ("gui/design_system/telemetry.py", "Phase 0 HELD"),
                ("gui/design_system/progress.py", "Phase 0 HELD"),
                ("gui/design_system/radar.py", "Phase 0 HELD"),
                ("gui/design_system/backdrop.py", "Phase 0 HELD"),
                ("gui/design_system/orbthumb.py", "Phase 0 HELD")):
            self.assertIn(needle, _src(rel), rel)

    def test_reminders_is_a_tab_not_a_page(self):
        src = _src("gui/window_pages_activity.py")
        self.assertIn('addTab(self._build_reminders_page(), "Scheduled")',
                      src)


class UiP0WindowTests(WindowBase):
    """Window-level Phase 0 assertions — need Qt."""

    def _show(self, index):
        from PySide6.QtWidgets import QApplication
        self.window._show_page(index)
        QApplication.instance().processEvents()

    def test_headers_are_kickers(self):
        from PySide6.QtWidgets import QLabel
        for index in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11):
            self._show(index)
            page = self.window.pages.widget(index)
            self.assertTrue(page.findChildren(QLabel, "pageKicker"),
                            f"stack {index}: no pageKicker")
            self.assertEqual(page.findChildren(QLabel, "pageTitle"), [],
                             f"stack {index}: pageTitle left")

    def test_home_wordmark_is_kicker_face(self):
        self._show(0)
        sheet = self.window.home_wordmark.styleSheet()
        self.assertIn("font-size: 13px", sheet)
        self.assertIn("font-weight: 600", sheet)

    def test_settings_switches_carry_labels(self):
        from voice_assistant.gui.design_system import HudSwitch
        self._show(4)
        switches = self.window.findChildren(HudSwitch)
        self.assertGreaterEqual(len(switches), 30)
        labels = [s.text() for s in switches]
        self.assertIn("Private mode", labels)
        self.assertIn("Interface sounds", labels)
        self.assertTrue(all(labels))

    def test_switch_toggles_and_paints(self):
        from voice_assistant.gui.design_system import HudSwitch
        from voice_assistant.gui.style import ACCENT
        self._show(4)
        sw = self.window.chk_sounds
        self.assertIsInstance(sw, HudSwitch)
        sw.setChecked(False)
        self.app.processEvents()
        off = sw.grab().toImage().pixelColor(20, 20).name().lower()
        sw.setChecked(True)
        self.app.processEvents()
        on = sw.grab().toImage().pixelColor(20, 20).name().lower()
        self.assertEqual(on, ACCENT.lower())
        self.assertNotEqual(off, ACCENT.lower())

    def test_switch_keyboard_focus(self):
        self._show(4)
        sw = self.window.chk_sounds
        sw.setFocus()
        self.app.processEvents()
        self.assertTrue(sw.hasFocus())
        # Paint with focus must not crash (the focus ring draws).
        sw.grab()

    def test_activity_tabs_are_hudtabs(self):
        from voice_assistant.gui.design_system import HudTabs
        self._show(1)
        bar = self.window._activity_tabs.tabBar()
        self.assertIsInstance(bar, HudTabs)
        self.assertIn("QTabBar::tab:selected", bar.styleSheet())

    def test_memory_empty_state(self):
        from voice_assistant.gui.design_system import EmptyState
        self._show(2)
        empty = self.window.mem_empty
        self.assertIsInstance(empty, EmptyState)
        self.assertTrue(empty.has_action)
        self.assertIn("hasn't learned anything", empty.sentence_text)
        empty.action_requested.emit()
        self.app.processEvents()
        self.assertTrue(self.window.input_mem_k.hasFocus())

    def test_activity_empty_states(self):
        # UI upgrade Phase 1 B-A03 (2026-11-24): the generic
        # EmptyState item is gone — each tab carries ONE designed
        # empty state (resting hollow ring + the tab's own line +
        # one quiet suggestion). In progress carries it on the
        # rail (missionRailEmpty); History carries its line.
        from PySide6.QtWidgets import QLabel
        self._show(1)
        self.window._refresh_activity_tabs()
        self.app.processEvents()
        rail_empty = self.window.findChild(QLabel, "missionRailEmpty")
        self.assertIsNotNone(rail_empty)
        self.assertTrue(rail_empty.isVisible())
        self.assertIn("Nothing in progress right now.",
                      rail_empty.text())
        self.assertIn("Ask VYUK to start something.", rail_empty.text())
        # Hunt fix (c) item 8: History carries ONE empty line —
        # the headline ("Nothing finished yet"). The list's
        # EmptyState ("No finished tasks yet.") is gone when there
        # are no terminal tasks at all.
        history = self.window.findChildren(QLabel)
        self.assertTrue(any(
            "Nothing finished yet" in lbl.text()
            for lbl in history))
        self.assertFalse(any(
            "No finished tasks yet." in lbl.text()
            for lbl in history))

    def test_reminders_empty_state(self):
        # UI upgrade Phase 1 B-A03 (2026-11-24): the Scheduled
        # timeline carries the designed empty state — "No
        # reminders yet." + "Add a reminder below." — not a
        # generic EmptyState item in the (hidden) list.
        from PySide6.QtWidgets import QLabel
        self._show(1)
        self.window._refresh_reminders()
        self.app.processEvents()
        labels = self.window.findChildren(QLabel)
        self.assertTrue(any(
            "No reminders yet." in lbl.text() for lbl in labels))
        self.assertTrue(any(
            "Add a reminder below." in lbl.text() for lbl in labels))

    def test_memory_detail_is_drawer(self):
        from voice_assistant.gui.design_system import ContextDrawer
        self._show(2)
        detail = getattr(self.window, "mem_web_detail", None)
        self.assertIsInstance(detail, ContextDrawer)
        detail.set_pinned(True)
        self.assertTrue(detail.pinned)

    def test_drawer_pin_holds_on_clear(self):
        self._show(2)
        detail = self.window.mem_web_detail
        # isHidden (not isVisible): the detail lives in the Web
        # view, which List view keeps hidden — the pin logic is
        # about the panel's own hide, not its ancestors.
        detail.setVisible(True)
        detail.set_pinned(True)
        self.window._on_web_selection_cleared()
        self.assertFalse(detail.isHidden())
        detail.set_pinned(False)
        self.window._on_web_selection_cleared()
        self.assertTrue(detail.isHidden())

    def test_calls_outcome_is_pill(self):
        from voice_assistant.core import voice_agent
        from voice_assistant.gui.design_system import StatusPill
        from PySide6.QtWidgets import QLabel
        import time
        store = voice_agent.VoiceAgentStore()
        agent = store.add_agent("P0 Suite Agent", "Hello", None, None, None)
        started = time.time() - 65
        store.record_call("out", "+44 7000 000010", agent["id"],
                          started, started + 65, "answered", [])
        try:
            self._show(11)
            # INTENTIONAL UPDATE (UI Phase 3, 2026-11-25): the
            # QTableWidget is a timeline of cards now (the same
            # shape as test_stage0f1.py's calls test). The
            # outcome is the card's StatusPill — text plus the
            # honest severity from _outcome_severity — and the
            # word appears nowhere else on the card (the title
            # is number + agent, the meta is direction + times),
            # so the old table-item UserRole / display-text
            # asserts have no subject any more and are dropped.
            timeline = self.window._views_calls_timeline
            cards = [timeline.itemAt(i).widget()
                     for i in range(timeline.count())]
            cards = [c for c in cards if c is not None]
            self.assertGreater(len(cards), 0)
            matches = [
                c for c in cards
                if any("+44 7000 000010" in lbl.text()
                       for lbl in c.findChildren(QLabel))]
            self.assertEqual(len(matches), 1)
            pill = matches[0].findChild(StatusPill)
            self.assertIsInstance(pill, StatusPill)
            self.assertEqual(pill.text, "answered")
            self.assertEqual(pill.severity, "success")
        finally:
            store.conn.execute(
                "DELETE FROM calls WHERE number = '+44 7000 000010'")
            store.conn.execute(
                "DELETE FROM agents WHERE name = 'P0 Suite Agent'")
            store.conn.commit()

    def test_toast_helper_and_web_forget(self):
        from voice_assistant.gui.design_system import HudToast
        import voice_assistant.gui.window_memory_web as _web
        self._show(2)
        self.assertTrue(callable(getattr(self.window, "show_toast", None)))
        # A real HudToast rises from the helper.
        self.window.show_toast("Phase 0 suite toast.")
        self.app.processEvents()
        toasts = self.window.findChildren(HudToast)
        self.assertTrue(any(t.text == "Phase 0 suite toast."
                            for t in toasts))
        for t in toasts:
            t.hide()
        # The web forget announces itself: the delete is stubbed
        # (the toast is the assertion, not the store), the id is
        # fake, the category is a plain one.
        self.window._web_selected_fact = {
            "id": 424242, "category": "general"}
        self.window._web_forget_armed = True
        with mock.patch.object(_web, "memory_store") as store:
            store.delete_fact.return_value = True
            with mock.patch.object(self.window, "show_toast") as toast:
                self.window._forget_web_fact()
        toast.assert_called_once_with("Forgot this fact.")

    def test_theme_seams_resolve_light(self):
        from voice_assistant.gui.design_system import (
            HudSwitch, HudTabs, HudToast, StatusPill)
        from voice_assistant.gui.style import THEMES as STYLE_THEMES
        from voice_assistant.gui.style_theme import _resolve_theme
        resolved = _resolve_theme(STYLE_THEMES["Light"])
        sw = HudSwitch(text="seam")
        sw.set_theme(resolved)
        self.assertEqual(sw._track, resolved["surface_2"])
        tabs = HudTabs()
        tabs.set_theme(resolved)
        self.assertIn(resolved["accent"], tabs.styleSheet())
        toast = HudToast(self.window, "seam", animate=False)
        toast.set_theme(resolved)
        self.assertIn(resolved["surface_1"], toast.styleSheet())
        pill = StatusPill(text="seam", severity="success")
        pill.set_theme(resolved)
        self.assertIn(resolved["text"], pill._label.styleSheet())

    def test_lazy_pill_and_toast_follow_light_theme(self):
        # REGRESSION (UI Phase 0, render-found): a StatusPill
        # is born per Calls refresh and a HudToast per
        # show_toast — both AFTER the appearance pass, so the
        # pass's findChildren never sees them and they painted
        # the house (near-white) text, invisible on Light
        # (Light 640 Calls render: glyphs only, words gone).
        # Both theme themselves from the window's stored
        # resolved theme at creation.
        from voice_assistant.core.config_spec import qsettings_set
        from voice_assistant.core import voice_agent
        from voice_assistant.gui.style import THEMES as STYLE_THEMES
        from voice_assistant.gui.style_theme import _resolve_theme
        import time
        resolved = _resolve_theme(STYLE_THEMES["Light"])
        qsettings_set("theme", "Light", settings=self.window.settings)
        self.window._apply_appearance()
        self.app.processEvents()
        store = voice_agent.VoiceAgentStore()
        agent = store.add_agent(
            "P0 Theme Agent", "Hello", None, None, None)
        started = time.time() - 65
        store.record_call("out", "+44 7000 000011", agent["id"],
                          started, started + 65, "answered", [])
        try:
            self._show(11)
            # INTENTIONAL UPDATE (UI Phase 3, 2026-11-25): the
            # QTableWidget is a timeline of cards now — the pill
            # is the card's StatusPill (same shape as
            # test_calls_outcome_is_pill above).
            from PySide6.QtWidgets import QLabel
            from voice_assistant.gui.design_system import StatusPill
            timeline = self.window._views_calls_timeline
            cards = [timeline.itemAt(i).widget()
                     for i in range(timeline.count())]
            cards = [c for c in cards if c is not None]
            matches = [
                c for c in cards
                if any("+44 7000 000011" in lbl.text()
                       for lbl in c.findChildren(QLabel))]
            self.assertEqual(len(matches), 1)
            pill = matches[0].findChild(StatusPill)
            self.assertIsNotNone(pill)
            self.assertIn(resolved["text"],
                          pill._label.styleSheet())
            self.window.show_toast("Forgot this fact.")
            toast = self.window._active_toast
            self.assertIsNotNone(toast)
            self.assertIn(resolved["surface_1"],
                          toast.styleSheet())
            self.assertIn(resolved["text"], toast.styleSheet())
        finally:
            store.conn.execute(
                "DELETE FROM calls WHERE number = '+44 7000 000011'")
            store.conn.execute(
                "DELETE FROM agents WHERE name = 'P0 Theme Agent'")
            store.conn.commit()
            qsettings_set("theme", "Cyan",
                          settings=self.window.settings)
            self.window._apply_appearance()
            self.app.processEvents()

    def test_fifteen_pages_scheduled_is_a_tab(self):
        # INTENTIONAL UPDATE (UI Phase 5, 2026-11-27): fifteen
        # pages — Notes, Finance and Devices join as first-class
        # workspaces (stack 12/13/14) before Settings.
        self.assertEqual(self.window.pages.count(), 15)
        self._show(1)
        names = [self.window._activity_tabs.tabText(i)
                 for i in range(self.window._activity_tabs.count())]
        self.assertIn("Scheduled", names)

    def test_inbox_drawer_held(self):
        # Phase 0 HELD (Phase 3): the reading pane is a permanent
        # half of the list+body split — the drawer's pin has no
        # honest meaning there yet. It stays a QTextBrowser.
        from PySide6.QtWidgets import QTextBrowser
        self._show(9)
        self.assertIsInstance(self.window._views_inbox_body, QTextBrowser)

    def test_doctor_pills_held(self):
        # Phase 0 HELD (Phase 4): the Doctor checklist rework owns
        # the pills; the hero counts line stays a QLabel, pinned
        # by test_rethink_build6.
        from PySide6.QtWidgets import QLabel
        self._show(7)
        self.assertIsInstance(self.window.doctor_page.hero_counts, QLabel)


if __name__ == "__main__":
    unittest.main()
