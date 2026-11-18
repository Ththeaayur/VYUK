"""Rethink Build 6: wizard permissions status list, Memory toolbar,
Doctor hero + typed reset, System Pulse rows, Senses rows.

Every behaviour change the build made, pinned against the real
widgets — not the plan's inventory, which the fresh renders
corrected in several places (no switches on Senses, no "when" on
Memory rows, four equal Doctor buttons with one destructive).

Qt only: the whole suite needs widgets, so without PySide6 every
class skips.
"""

import os
import pathlib
import sys
import tempfile
import time
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import PySide6  # noqa: F401 — availability probe only
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from tests.test_chat_ui import WindowBase
    from tests.test_first_run_onboarding import _DataGuard
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    WindowBase = object  # type: ignore[assignment]

# Filled by setUpModule (after the voice_assistant.* alias):
# gui/* imports voice_assistant.core.* (pre-split paths), so the
# GUI classes can only be imported once the alias exists — the
# test_first_run_onboarding shape. Module-level imports would run
# before it and fail with ModuleNotFoundError.
OnboardingDialog = None
PulsePage = None
_metric_word = None
SensesPage = None
_mem_recency_group = None
_mem_when_text = None
_TypedConfirmDialog = None
permission_grants = None
_qsettings_set = None
_VA_SNAPSHOT = None


def setUpModule():
    """Mirror test_first_run_onboarding's alias, then import the
    GUI classes. Snapshot discipline: restore prior entries on
    teardown so a co-run suite's cached modules survive."""
    global _VA_SNAPSHOT
    global OnboardingDialog, PulsePage, _metric_word, SensesPage
    global _mem_recency_group, _mem_when_text, _TypedConfirmDialog
    global permission_grants, _qsettings_set
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    if not _QT:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    tree = str(ROOT)
    pkg = types.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = types.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    sys.modules["voice_assistant"] = pkg
    sys.modules["voice_assistant.core"] = core
    from gui.onboarding import OnboardingDialog as _OD  # noqa: E402
    from gui.pulse_page import (  # noqa: E402
        PulsePage as _PP, _metric_word as _mw)
    from gui.senses_page import SensesPage as _SP  # noqa: E402
    from gui.window_memory_web import (  # noqa: E402
        _mem_recency_group as _mrg, _mem_when_text as _mwt)
    from gui.window_pages_system import (  # noqa: E402
        _TypedConfirmDialog as _TCD)
    from voice_assistant.core.gui_facade import (  # noqa: E402
        permission_grants as _pg, qsettings_set as _qs)
    OnboardingDialog = _OD
    PulsePage = _PP
    _metric_word = _mw
    SensesPage = _SP
    _mem_recency_group = _mrg
    _mem_when_text = _mwt
    _TypedConfirmDialog = _TCD
    permission_grants = _pg
    _qsettings_set = _qs


def tearDownModule():
    if _VA_SNAPSHOT is None:
        return
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        if key not in _VA_SNAPSHOT:
            del sys.modules[key]
    sys.modules.update(_VA_SNAPSHOT)


def _app():
    return QApplication.instance() or QApplication([])


@unittest.skipUnless(_QT, "Qt unavailable")
class _GrantsMixin(unittest.TestCase):
    """Temp-dir grants store (the test_first_run_onboarding shape):
    every grant decision in these tests lands in a temp file, never
    the real store, and the session grants start empty."""

    def setUp(self):
        super().setUp()
        _app()
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) \
            / "permission_grants.json"
        pg = permission_grants
        if pg is None:
            from core import permission_grants as _flat_pg
            pg = _flat_pg
        self._pg = pg
        pg.set_path_override(self._store)
        pg.clear_session_grants()
        # The sounds opt-in is QSettings, shared across the
        # process: an earlier window test leaves it on, so the
        # row would read "On" at construction. Reset it — False
        # is the product default (the wizard writes it on Finish).
        if _qsettings_set is not None:
            _qsettings_set("sounds_enabled", False)
        self._guard = _DataGuard()
        self._guard.__enter__()

    def tearDown(self):
        self._guard.__exit__()
        self._pg.clear_path_override()
        self._pg.clear_session_grants()
        self._tmp.cleanup()
        super().tearDown()

    def _dialog(self):
        dlg = OnboardingDialog()
        self.addCleanup(dlg.deleteLater)
        return dlg

    def _perm_page(self, dlg):
        dlg.pages.setCurrentWidget(dlg._permissions_page)
        dlg._refresh_permissions_page()
        return dlg._permissions_page


# ------------------------------------------------------------------ wizard
@unittest.skipUnless(_QT, "Qt unavailable")
class WizardPermissionsTests(_GrantsMixin):
    """The permissions page as a status list: one row per
    permission, state in plain words, no explainer paragraphs."""

    def test_rows_cover_every_permission_and_nothing_else(self):
        dlg = self._dialog()
        self._perm_page(dlg)
        self.assertEqual(
            sorted(dlg._perm_rows),
            ["browser", "camera", "files", "home_devices",
             "microphone", "screen"])
        # Sounds is a row too (D12) but not a grant — it lives in
        # its own widgets, not the permission rows.
        self.assertEqual(dlg._sounds_state.text(), "Off")
        self.assertEqual(dlg._sounds_action.text(), "Turn on")

    def test_state_words_fresh_install(self):
        dlg = self._dialog()
        self._perm_page(dlg)
        for pid in dlg._perm_rows:
            self.assertEqual(dlg._perm_rows[pid]["state"].text(),
                             "Never decided", pid)

    def test_state_words_granted_denied_session(self):
        dlg = self._dialog()
        self._pg.set_permission_state("microphone", "granted")
        self._pg.set_permission_state("camera", "denied")
        self._pg.grant_session_only("screen")
        self._perm_page(dlg)
        rows = dlg._perm_rows
        self.assertEqual(rows["microphone"]["state"].text(), "On")
        self.assertEqual(rows["camera"]["state"].text(), "Off")
        self.assertEqual(rows["screen"]["state"].text(),
                         "This session only")

    def test_take_back_durable_grant_undecides_it(self):
        dlg = self._dialog()
        self.assertEqual(dlg._on_mic_ask("always"), "always")
        self.assertEqual(
            self._pg.get_state("microphone"), "granted")
        self._perm_page(dlg)
        rows = dlg._perm_rows
        self.assertEqual(rows["microphone"]["state"].text(), "On")
        self.assertEqual(rows["microphone"]["action"].text(), "Take back")
        rows["microphone"]["action"].click()
        self.assertTrue(
            self._pg.permission_never_decided("microphone"))
        self.assertEqual(rows["microphone"]["state"].text(),
                         "Never decided")
        # The walk grant is gone too: a later use re-asks.
        self.assertNotIn("microphone", dlg._walk_grants)

    def test_take_back_session_grant(self):
        dlg = self._dialog()
        self.assertEqual(dlg._on_mic_ask("allow_once"), "allow_once")
        self.assertTrue(self._pg.has_session_grant("microphone"))
        self._perm_page(dlg)
        rows = dlg._perm_rows
        self.assertEqual(rows["microphone"]["state"].text(),
                         "This session only")
        rows["microphone"]["action"].click()
        self.assertFalse(self._pg.has_session_grant("microphone"))
        self.assertEqual(rows["microphone"]["state"].text(),
                         "Never decided")

    def test_take_back_files_removes_the_chosen_folder(self):
        dlg = self._dialog()
        self._pg.set_permission_state("files", "granted")
        self.assertTrue(
            self._pg.add_folder("/tmp/somewhere", via="settings"))
        dlg._files_page.chosen_folder = "/tmp/somewhere"
        dlg._record_walk_grant("files", "durable")
        self._perm_page(dlg)
        rows = dlg._perm_rows
        self.assertEqual(rows["files"]["action"].text(), "Take back")
        rows["files"]["action"].click()
        self.assertTrue(self._pg.permission_never_decided("files"))
        self.assertNotIn("/tmp/somewhere", self._pg.list_folders())
        self.assertNotIn("files", dlg._walk_grants)

    def test_ask_me_navigates_to_the_ask_page(self):
        dlg = self._dialog()
        self._perm_page(dlg)
        rows = dlg._perm_rows
        self.assertEqual(rows["camera"]["action"].text(), "Ask me")
        rows["camera"]["action"].click()
        self.assertIs(dlg.pages.currentWidget(), dlg._camera_page)

    def test_sounds_row_toggles_the_opt_in(self):
        dlg = self._dialog()
        self._perm_page(dlg)
        self.assertFalse(dlg._sound_checkbox.isChecked())
        dlg._sounds_action.click()
        self.assertTrue(dlg._sound_checkbox.isChecked())
        self.assertEqual(dlg._sounds_state.text(), "On")
        self.assertEqual(dlg._sounds_action.text(), "Turn off")
        dlg._sounds_action.click()
        self.assertFalse(dlg._sound_checkbox.isChecked())
        self.assertEqual(dlg._sounds_state.text(), "Off")

    def test_mic_row_stays_live_in_the_same_walk(self):
        # Fix #29's hook survives the rebuild: grant the mic on
        # the mic ask page, reach the permissions page, the mic
        # row says on — with the pinned diagnostic labels filled.
        dlg = self._dialog()
        self.assertEqual(dlg._on_mic_ask("always"), "always")
        self._perm_page(dlg)
        self.assertEqual(dlg._perm_rows["microphone"]["state"].text(),
                         "On")
        self.assertTrue(dlg._ob_mic_vyuk.text())
        self.assertTrue(dlg._ob_mic_os.text())
        self.assertTrue(dlg._ob_mic_diag.text())

    def test_advanced_setup_is_a_quiet_link_not_a_button(self):
        dlg = self._dialog()
        self.assertEqual(dlg.btn_advanced.text(), "Advanced setup")
        dlg.btn_advanced.click()
        self.assertEqual(dlg.btn_advanced.text(), "Back to setup")
        dlg.btn_advanced.click()
        self.assertEqual(dlg.btn_advanced.text(), "Advanced setup")

    def test_no_memory_or_cloud_explainers(self):
        dlg = self._dialog()
        page = self._perm_page(dlg)
        from PySide6.QtWidgets import QLabel
        texts = [lb.text() for lb in page.findChildren(QLabel)]
        self.assertFalse(any("Cloud" in t for t in texts))
        self.assertFalse(any("Remember everything" in t for t in texts))


# ------------------------------------------------------------------ memory
@unittest.skipUnless(_QT, "Qt unavailable")
class MemoryBuild6Tests(WindowBase):
    """The Memory page: one toolbar, recency groups, selection bar,
    one-tap forget with undo. (Header-count behaviour from fix #28
    is pinned by test_memory_header_count; this pins Build 6.)"""

    def setUp(self):
        super().setUp()
        self._orig_feed = os.environ.get("VYUK_LEARNING_FEED_DIR")
        os.environ["VYUK_LEARNING_FEED_DIR"] = str(self._tmp / "feed")
        import voice_assistant.core.gui_facade as facade
        self._facade = facade
        facade.memory_store = self._store
        from core.memory import learning as module
        self._learn = module
        self._orig_learn_store = module.memory_store
        module.memory_store = self._store
        self.window._learning_module = lambda: module

    def tearDown(self):
        try:
            self._learn.memory_store = self._orig_learn_store
        except Exception:  # noqa: BLE001 — best-effort restore
            pass
        try:
            delattr(self._facade, "memory_store")
        except Exception:  # noqa: BLE001 — best-effort restore
            pass
        if self._orig_feed is None:
            os.environ.pop("VYUK_LEARNING_FEED_DIR", None)
        else:
            os.environ["VYUK_LEARNING_FEED_DIR"] = self._orig_feed
        super().tearDown()

    def _teach(self, text):
        stored = self._learn.learn_from_text(text)
        self.assertEqual(len(stored), 1, f"did not store: {text!r}")
        return stored[0]

    def _show_memory(self):
        self.window._show_page(2)
        self.app.processEvents()

    def _fact_rows(self):
        return sum(
            1 for i in range(self.window.memory_list.count())
            if self.window.memory_list.item(i).data(
                Qt.ItemDataRole.UserRole) is not None)

    def _headers(self):
        return [self.window.memory_list.item(i).text()
                for i in range(self.window.memory_list.count())
                if self.window.memory_list.item(i).data(
                    Qt.ItemDataRole.UserRole) is None]

    def test_toolbar_is_one_row(self):
        self.window.show()
        self._show_memory()
        win = self.window
        self.assertTrue(win.mem_search.isVisible())
        self.assertEqual(win.btn_mem_filter.text(), "Filter: All")
        self.assertTrue(win.btn_mem_view_list.isVisible())
        self.assertTrue(win.btn_mem_view_web.isVisible())
        self.assertEqual(win.btn_mem_overflow.text(), "···")
        # The category combo is a hidden state holder now, not a
        # visible control; the old "Clear conversation history"
        # button is gone from the page bottom.
        self.assertFalse(win.mem_cat_filter.isVisible())
        self.assertFalse(hasattr(win, "btn_clear_history")
                         and win.btn_clear_history.isVisible())

    def test_list_is_grouped_by_recency_with_a_when(self):
        self._teach("remember that my dentist is Dr. Rao")
        self._show_memory()
        self.assertEqual(self._fact_rows(), 1)
        self.assertEqual(self._headers(), ["TODAY"])
        from PySide6.QtWidgets import QListWidgetItem
        row = self.window.memory_list.item(1)
        widget = self.window.memory_list.itemWidget(row)
        self.assertIsNotNone(widget)
        self.assertIn("today", widget.when.text().lower())

    def test_selection_bar_forget_and_undo(self):
        self._teach("remember that my dentist is Dr. Rao")
        self._teach("remember that my favourite colour is azure")
        self._show_memory()
        self.assertEqual(self._fact_rows(), 2)
        win = self.window
        self.assertTrue(win._mem_sel_bar.isHidden())
        # Select the first fact row (item 1: item 0 is the header).
        win.memory_list.item(1).setSelected(True)
        self.app.processEvents()
        self.assertFalse(win._mem_sel_bar.isHidden())
        self.assertIn("1 selected", win._mem_sel_label.text())
        win._forget_selected_memory()
        self.app.processEvents()
        self.assertEqual(self._fact_rows(), 1)
        self.assertFalse(win._mem_undo_bar.isHidden())
        win._undo_mem_forget()
        self.app.processEvents()
        self.assertEqual(self._fact_rows(), 2)
        self.assertTrue(win._mem_undo_bar.isHidden())

    def test_filter_menu_choice_narrows_the_list(self):
        self._teach("remember that my dentist is Dr. Rao")
        self._store.save_fact("general", "Favourite drink", "Masala tea")
        self._show_memory()
        self.assertEqual(self._fact_rows(), 2)
        # The menu's category action sets the (hidden) combo's
        # text; the list re-reads it on refresh.
        self.window.mem_cat_filter.setCurrentText("learned")
        self.window._refresh_memories()
        self.assertEqual(self._fact_rows(), 1)
        self.assertIn("learned", self.window.btn_mem_filter.text())


@unittest.skipUnless(_QT, "Qt unavailable")
class MemoryWhenTests(unittest.TestCase):
    """The recency grouping and the 'when' are pure functions of
    the fact's timestamp — pinned here against fixed epochs so a
    slow clock can never flip them."""

    def test_when_text(self):
        now = time.time()
        self.assertEqual(_mem_when_text(now), "today")
        self.assertEqual(_mem_when_text(now - 86_400), "yesterday")
        old = time.mktime(time.strptime("2025-11-12", "%Y-%m-%d"))
        self.assertEqual(_mem_when_text(old), "12 Nov 2025")
        self.assertEqual(_mem_when_text(None), "")

    def test_recency_groups(self):
        now = time.time()
        self.assertEqual(_mem_recency_group(now), "Today")
        self.assertEqual(_mem_recency_group(now - 3 * 86_400),
                         "This week")
        self.assertEqual(_mem_recency_group(now - 30 * 86_400),
                         "Earlier")
        self.assertEqual(_mem_recency_group(None), "Earlier")


# ------------------------------------------------------------------ doctor
@unittest.skipUnless(_QT, "Qt unavailable")
class DoctorBuild6Tests(WindowBase):
    """The Doctor hero headline and the typed reset gate."""

    def _show_doctor(self):
        self.window._show_page(7)
        self.app.processEvents()

    def _wait_for_run(self, deadline_s=60.0):
        # The page runs its checks on show; wait for the hero to
        # leave "Checking…" on MEASURED time, never on pumps.
        start = time.monotonic()
        while time.monotonic() - start < deadline_s:
            if self.window.doctor_page.hero_title.text() != "Checking…":
                return True
            self.app.processEvents()
            time.sleep(0.05)
        return False

    def test_hero_headline_healthy_or_n_issues(self):
        self._show_doctor()
        self.assertTrue(self._wait_for_run(),
                        "the Doctor run never finished")
        page = self.window.doctor_page
        # 20 px semibold at every site, whatever the headline says.
        self.assertIn("font-size: 20px", page.hero_title.styleSheet())
        self.assertIn("font-weight: 600", page.hero_title.styleSheet())
        page._on_done({"ok": True, "counts": {"PASS": 40, "WARN": 0,
                                               "FAIL": 0},
                       "findings": [], "text": ""})
        self.assertEqual(page.hero_title.text(), "Healthy")
        page._on_done({"ok": True, "counts": {"PASS": 40, "WARN": 25,
                                               "FAIL": 3},
                       "findings": [], "text": ""})
        self.assertEqual(page.hero_title.text(), "28 issues")
        self.assertIn("40 checks passed", page.hero_counts.text())

    def test_findings_are_rows_not_cards(self):
        self._show_doctor()
        self.assertTrue(self._wait_for_run(),
                        "the Doctor run never finished")
        page = self.window.doctor_page
        # The live run finished with findings: every finding frame
        # in the findings layout is a ROW (horizontal layout),
        # not the old stacked card.
        from PySide6.QtWidgets import QHBoxLayout, QFrame
        frames = []
        for i in range(page.findings_layout.count()):
            widget = page.findings_layout.itemAt(i).widget()
            if isinstance(widget, QFrame) and widget.objectName() \
                    == "settingsCard":
                frames.append(widget)
        self.assertGreater(len(frames), 0,
                           "no finding rows rendered")
        for frame in frames:
            self.assertIsInstance(frame.layout(), QHBoxLayout)

    def test_reset_is_red_text_with_a_typed_gate(self):
        self._show_doctor()
        win = self.window
        btn = win.btn_w10_reset
        self.assertEqual(btn.text(), "Reset settings")
        self.assertTrue(btn.isFlat())
        from gui.style import DANGER
        self.assertIn(DANGER, btn.styleSheet())
        dialog = _TypedConfirmDialog(win)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog._reset_btn.isEnabled())
        dialog._edit.setText("Reset settings")
        self.assertFalse(dialog._reset_btn.isEnabled())
        dialog._edit.setText("Reset")
        self.assertTrue(dialog._reset_btn.isEnabled())
        dialog._edit.setText(" Reset ")
        self.assertTrue(dialog._reset_btn.isEnabled())
        dialog._edit.setText("reset")
        self.assertFalse(dialog._reset_btn.isEnabled())


# ------------------------------------------------------------------- pulse
@unittest.skipUnless(_QT, "Qt unavailable")
class PulseBuild6Tests(unittest.TestCase):
    """System Pulse as rows: 7 rows x 48 px, all 8 numbers, the
    word naming the worst metric."""

    _SAMPLE = {"cpu": 53.0, "mem": 89.0, "disk": 7.0,
               "net_bps": 61_100_000.0, "battery_pct": 87.0,
               "battery_plugged": True, "uptime_s": 3300.0,
               "procs": 6}

    def _page(self, sample=None):
        _app()
        page = PulsePage(sampler=lambda: dict(sample or self._SAMPLE))
        self.addCleanup(page.deleteLater)
        page.sample_now()
        return page

    def test_seven_rows_48px(self):
        page = self._page()
        self.assertEqual(len(page._rows), 7)
        self.assertEqual([r.key for r in page._rows],
                         ["cpu", "mem", "disk", "net", "battery",
                          "uptime", "procs"])
        for row in page._rows:
            self.assertEqual(row.frame.minimumHeight(), 48)
            self.assertEqual(row.frame.maximumHeight(), 48)
        self.assertEqual(sorted(page.dials),
                         ["battery", "cpu", "disk", "mem", "net"])

    def test_all_eight_numbers_present(self):
        page = self._page()
        values = {r.key: r.value_label.text() for r in page._rows}
        self.assertEqual(values, {"cpu": "53%", "mem": "89%",
                                  "disk": "7%", "net": "61.1 MB/s",
                                  "battery": "87%", "uptime": "55m",
                                  "procs": "6"})

    def test_word_names_the_worst_metric(self):
        self.assertEqual(_metric_word(dict(self._SAMPLE)),
                         ("Ready", "ok"))
        hot = dict(self._SAMPLE, cpu=95.0)
        self.assertEqual(_metric_word(hot), ("CPU high", "warning"))
        full = dict(self._SAMPLE, disk=96.0)
        self.assertEqual(_metric_word(full),
                         ("Disk almost full", "error"))
        low = dict(self._SAMPLE, battery_pct=8.0,
                   battery_plugged=False)
        self.assertEqual(_metric_word(low), ("Battery low", "error"))
        # A charging battery at 8% is not "Battery low".
        charging = dict(self._SAMPLE, battery_pct=8.0,
                        battery_plugged=True)
        self.assertEqual(_metric_word(charging), ("Ready", "ok"))
        page = self._page(hot)
        self.assertEqual(page.word_label.text(), "CPU high")

    def test_footnote_intact(self):
        page = self._page()
        self.assertIn("System probes only", page.bars_note.text())
        self.assertIn("no news or entity cards here",
                      page.bars_note.text())

    def test_unknown_reads_dash(self):
        page = self._page({key: None for key in self._SAMPLE})
        for row in page._rows:
            self.assertEqual(row.value_label.text(), "—", row.key)
        self.assertEqual(page.word_label.text(), "Ready")

    def test_stats_line_appears_after_two_readings(self):
        page = self._page()
        page.sample_now()
        # isVisible() is False for children of an unshown page;
        # isHidden() is the honest check for the label's own flag.
        self.assertFalse(
            page._row_by_key["cpu"].stats_label.isHidden())
        self.assertIn("avg", page._row_by_key["cpu"].stats_label.text())


# ------------------------------------------------------------------ senses
@unittest.skipUnless(_QT, "Qt unavailable")
class SensesBuild6Tests(_GrantsMixin):
    """The Senses page: three sense rows, state only, one-tap way
    to Settings → Permissions. No switches anywhere."""

    def test_three_rows_no_switches_no_buttons_standalone(self):
        _app()
        page = SensesPage(providers={})
        self.addCleanup(page.deleteLater)
        self.assertEqual(sorted(page._sense_rows),
                         ["camera", "microphone", "screen"])
        from PySide6.QtWidgets import QCheckBox
        self.assertEqual(page.findChildren(QCheckBox), [])
        # No callback, no buttons — never a dimmed button.
        self.assertEqual(page._sense_buttons, {})

    def test_state_words_follow_the_grants(self):
        _app()
        page = SensesPage(providers={})
        self.addCleanup(page.deleteLater)
        words = {pid: lb.text()
                 for pid, lb in page._sense_rows.items()}
        self.assertEqual(words, {"microphone": "Off", "camera": "Off",
                                 "screen": "Off"})
        self._pg.set_permission_state("microphone", "granted")
        self._pg.grant_session_only("camera")
        page.refresh_state()
        words = {pid: lb.text()
                 for pid, lb in page._sense_rows.items()}
        self.assertEqual(words["microphone"], "On")
        self.assertEqual(words["camera"], "This session only")
        self.assertEqual(words["screen"], "Off")


@unittest.skipUnless(_QT, "Qt unavailable")
class SensesWindowTests(WindowBase):
    """The Senses Permissions button opens the exact card."""

    def test_permissions_button_opens_the_permissions_card(self):
        self.window._show_page(6)
        self.app.processEvents()
        page = self.window.senses_page
        self.assertIsNotNone(page)
        self.assertEqual(sorted(page._sense_buttons),
                         ["camera", "microphone", "screen"])
        calls = []
        orig = self.window.show_settings_card
        self.window.show_settings_card = lambda title: calls.append(
            title)
        try:
            page._sense_buttons["microphone"].click()
        finally:
            self.window.show_settings_card = orig
        self.assertEqual(calls, ["Permissions"])


if __name__ == "__main__":
    unittest.main()
