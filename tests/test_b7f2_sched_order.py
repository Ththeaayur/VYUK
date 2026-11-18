"""B7F2: the Scheduled tab shows the routines section — Morning
briefing card first, then Scheduled routines — ABOVE the Reminders
list (Build 7 Finding 2).

The finding, measured pre-fix by the B7F2 probe
(b7f2_sched_work/probe_sched.py, dense morning: 4 reminders,
2 routines, 2 plans): at 1280x800 the scroll viewport is 410 px
against 750 px of content, so the "Scheduled routines" title sat
46 px below the fold and the Morning briefing time control was
cut mid-row (top 398, bottom 438, fold at 410); at 1024x768 the
same fold (content 767 px — the routines hint wraps to two
lines). The tab is named Scheduled, so its first-class content
is the scheduled items. The fix moves the two blocks — Morning
briefing, then Scheduled routines — above the Reminders section
in _build_reminders_page; nothing is shortened, no size changes,
the Reminders list, the headline counts and the "?" help button
are untouched (the "?" is fix #3; the citizenship card is fix #4).

Qt only: the whole suite needs widgets, so without PySide6 every
class skips (the test_chat_ui / test_rethink_build6 shape).
"""

import datetime
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase
    from tests.test_scifi_activity import _dense_records
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    # TestCase, not object: the loader only collects TestCase
    # subclasses, so an object base would run ZERO tests under
    # sys (rc 5, "NO TESTS RAN") instead of 5 skipped — the
    # test_rethink_build6 shape, where the mixin is a TestCase.
    WindowBase = unittest.TestCase  # type: ignore[assignment]


def _pump(app, seconds=0.6):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.05)
    app.processEvents()


def _seed_scheduled():
    """The dense morning from test_scifi_activity._seed_dense:
    7 ledger records, 3 reminders (+1 fired), 2 routines, 2 plans
    — the seeding the B7F2 probe measured the finding with."""
    import voice_assistant.core.gui_facade as facade

    data_dir = ROOT / "data"
    (data_dir / "task_ledger.json").write_text(
        json.dumps({"tasks": _dense_records()}), encoding="utf-8")
    (data_dir / "plans.json").write_text(json.dumps([
        {"id": "plan-1", "goal": "Prepare the quarterly review",
         "steps": [
             {"text": "Gather the numbers", "status": "done"},
             {"text": "Draft the summary", "status": "done"},
             {"text": "Send it to the team", "status": "pending"}]},
        {"id": "plan-2", "goal": "Plan the weekend trip",
         "steps": [
             {"text": "Pick the destination", "status": "done"},
             {"text": "Book the train", "status": "running"}]},
    ]), encoding="utf-8")
    _stub_scheduler = sys.modules.pop(
        "voice_assistant.core.scheduler", None)
    try:
        from voice_assistant.core.scheduler import ProactiveScheduler
    finally:
        if _stub_scheduler is not None:
            sys.modules["voice_assistant.core.scheduler"] = (
                _stub_scheduler)
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="b7f2_test_sched_"))
    today = datetime.datetime.now().date()
    real = ProactiveScheduler(
        store_path=tmpdir / "reminders.json",
        now_fn=lambda: datetime.datetime.combine(
            today, datetime.time(1, 0)))
    real.add_reminder(
        "r1", datetime.datetime.combine(today, datetime.time(9, 0)),
        "Standup in ten minutes")
    real.add_reminder(
        "r2", datetime.datetime.combine(today, datetime.time(17, 0)),
        "Call the electrician")
    real.add_reminder(
        "r3", datetime.datetime.combine(
            today + datetime.timedelta(days=1), datetime.time(8, 0)),
        "Water the plants")
    real.add_reminder(
        "r4", datetime.datetime.combine(today, datetime.time(7, 30)),
        "Morning alarm")
    for stored in real._reminders:
        if stored.label == "r4":
            stored.fired = True
    result = facade.planner.save_routine(
        "Morning briefing run",
        actions=[{"tool": "set_reminder",
                  "args": {"message": "Standup in ten minutes",
                           "minutes": 10}}],
        schedule="daily 07:30")
    assert not str(result).startswith("Could not save"), result
    result = facade.planner.save_routine(
        "Evening tidy",
        actions=[{"tool": "set_reminder",
                  "args": {"message": "Tidy the desk",
                           "minutes": 5}}])
    assert not str(result).startswith("Could not save"), result
    facade.scheduler = real


@unittest.skipUnless(_QT, "Qt unavailable")
class SchedOrderTests(WindowBase):
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

    def _section_tops(self):
        """Section titles inside the Scheduled scroll content, in
        content coords: {name: (top, bottom)}."""
        from PySide6.QtWidgets import QLabel
        content = self.window.reminders_scroll.widget()
        tops = {}
        for lbl in content.findChildren(QLabel):
            if lbl.objectName() == "activitySection":
                tops[lbl.text()] = (lbl.y(), lbl.y() + lbl.height())
        return tops

    def _fold(self):
        """Viewport bottom at rest (bar value 0)."""
        scroll = self.window.reminders_scroll
        return (scroll.verticalScrollBar().value()
                + scroll.viewport().height())

    def _brief_control_bottom(self):
        """Bottom of the Morning briefing time control, in
        content coords."""
        from PySide6.QtCore import QPoint as _QPoint
        content = self.window.reminders_scroll.widget()
        spin = self.window.spin_brief_hour
        return spin.mapTo(content, _QPoint(0, spin.height())).y()

    def _assert_routines_above_reminders(self, width, height):
        self.window.resize(width, height)
        _pump(self.app, 0.8)
        tops = self._section_tops()
        for name in ("Reminders", "Morning briefing",
                     "Scheduled routines", "Plans"):
            self.assertIn(name, tops, f"missing section: {name}")
        fold = self._fold()
        self.assertLess(
            tops["Morning briefing"][0], tops["Reminders"][0],
            f"Morning briefing not above Reminders at {width}x{height}: "
            f"{tops}")
        self.assertLess(
            tops["Scheduled routines"][0], tops["Reminders"][0],
            f"Scheduled routines not above Reminders at "
            f"{width}x{height}: {tops}")
        self.assertLess(
            tops["Morning briefing"][1], fold,
            f"Morning briefing title below the fold at "
            f"{width}x{height}: {tops} fold={fold}")
        self.assertLessEqual(
            self._brief_control_bottom(), fold,
            f"Morning briefing time control cut at the fold at "
            f"{width}x{height}: bottom="
            f"{self._brief_control_bottom()} fold={fold}")
        self.assertLess(
            tops["Scheduled routines"][0], fold,
            f"Scheduled routines title below the fold at "
            f"{width}x{height}: {tops} fold={fold}")

    def test_routines_above_reminders_1280(self):
        # The finding's own geometry: 410 px viewport, 750 px of
        # content — routines 46 px below the fold, brief control
        # cut mid-row (398–438, fold at 410) pre-fix.
        self._assert_routines_above_reminders(1280, 800)

    def test_routines_above_reminders_1024(self):
        # Same fold at the narrower width (content 767 px — the
        # routines hint wraps to two lines) — the reorder must
        # hold there too.
        self._assert_routines_above_reminders(1024, 768)

    def test_reminders_still_present_and_working(self):
        # The reorder moves blocks; it must not lose the
        # Reminders section itself: 4 rows, Refresh, the add row,
        # the headline counts and the "?" (fix #3 owns the "?").
        self.window.resize(1280, 800)
        _pump(self.app, 0.8)
        self.assertEqual(self.window.reminders_list.count(), 4)
        self.assertEqual(
            self.window._activity_headlines["scheduled"]["line"].text(),
            "2 due today · next 09:00 · 1 already spoken")
        from PySide6.QtWidgets import QPushButton
        tab = self.window._activity_tabs.widget(1)
        refresh = [b for b in tab.findChildren(QPushButton)
                   if b.text() == "Refresh"]
        self.assertEqual(len(refresh), 1)
        helps = [b for b in tab.findChildren(QPushButton)
                 if b.text() == "?"]
        self.assertEqual(len(helps), 1)
        self.assertEqual(
            self.window.input_reminder_msg.placeholderText(),
            "Remind me to…")

    def test_plans_still_last(self):
        # Plans stays the last section — the reorder moves the
        # routines blocks up, it does not shuffle the tail.
        self.window.resize(1280, 800)
        _pump(self.app, 0.8)
        tops = self._section_tops()
        self.assertLess(
            tops["Reminders"][0], tops["Plans"][0],
            f"Plans no longer last: {tops}")
        rows = [self.window.plans_layout.itemAt(i).widget()
                for i in range(self.window.plans_layout.count())]
        self.assertEqual(len(rows), 2)

    def test_briefing_card_first_then_routines(self):
        # The ruled order inside the moved block: the Morning
        # briefing card first, then Scheduled routines.
        self.window.resize(1280, 800)
        _pump(self.app, 0.8)
        tops = self._section_tops()
        self.assertLess(
            tops["Morning briefing"][0],
            tops["Scheduled routines"][0],
            f"Morning briefing not first in the moved block: {tops}")
        rows = [self.window.routines_layout.itemAt(i).widget()
                for i in range(self.window.routines_layout.count())]
        self.assertEqual(len(rows), 2)
