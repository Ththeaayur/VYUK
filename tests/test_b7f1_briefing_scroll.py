"""B7F1: expanding the briefing card auto-scrolls the stage so the
expanded card is fully visible (Build 7 Finding 1).

The finding, measured pre-fix by the B7F1 probe
(b7f1_briefing_work/probe_briefing.py): at 1280x800 the stage
viewport is 551 px against 610 px of content, so the expanded
140 px card's bottom sat 59 px below the fold and the bar never
moved on the tap; at 1024x768 the card is 157 px (the schedule
wraps to two lines), content 644 px vs a 519 px viewport, and
the bottom sat 125 px below the fold. The fix scrolls the stage
once per expand tap, card bottom to viewport bottom, clamped to
the scrollbar maximum.

Qt only: the whole suite needs widgets, so without PySide6 every
class skips (the test_chat_ui / test_rethink_build6 shape).
"""

import datetime
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


def _seed_briefing_reminders():
    """Build 7's four reminders on a real ProactiveScheduler pinned
    to 01:00 so nothing fires mid-test — the same seeding the B7F1
    probe uses, so the schedule line (and the card height) match
    the finding: 'Calendar not connected · Reminders: Standup in
    ten minutes · Call the electrician · Water the plants'."""
    import voice_assistant.core.gui_facade as facade
    # The harness shim replaced voice_assistant.core.scheduler with
    # a stub in sys.modules (Build 7's render script does this same
    # pop/import/restore dance to reach the real ProactiveScheduler).
    _stub = sys.modules.pop("voice_assistant.core.scheduler", None)
    try:
        from voice_assistant.core.scheduler import ProactiveScheduler
    finally:
        if _stub is not None:
            sys.modules["voice_assistant.core.scheduler"] = _stub
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="b7f1_test_sched_"))
    today = datetime.datetime.now().date()
    real = ProactiveScheduler(
        store_path=tmpdir / "reminders.json",
        now_fn=lambda: datetime.datetime.combine(
            today, datetime.time(1, 0)))
    real.add_reminder("r1", datetime.datetime.combine(
        today, datetime.time(9, 0)), "Standup in ten minutes")
    real.add_reminder("r2", datetime.datetime.combine(
        today, datetime.time(17, 0)), "Call the electrician")
    real.add_reminder("r3", datetime.datetime.combine(
        today + datetime.timedelta(days=1), datetime.time(8, 0)),
        "Water the plants")
    real.add_reminder("r4", datetime.datetime.combine(
        today, datetime.time(7, 30)), "Morning alarm")
    for stored in real._reminders:
        if stored.label == "r4":
            stored.fired = True
    facade.scheduler = real
    import voice_assistant.gui.window_shell_briefing as _bmod
    _bmod.scheduler = real


@unittest.skipUnless(_QT, "Qt unavailable")
class BriefingScrollTests(WindowBase):
    """The real MainWindow offscreen (WindowBase), the briefing
    card seeded the way the finding measured it."""

    def setUp(self):
        super().setUp()
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — boot skip is best-effort
            pass
        _seed_briefing_reminders()
        self.window._refresh_briefing_card()
        _pump(self.app, 0.5)

    def _show_at(self, width, height):
        self.window.resize(width, height)
        _pump(self.app, 0.8)

    def _geometry(self):
        w = self.window
        scroll = w._stage_scroll
        bar = scroll.verticalScrollBar()
        card = w.briefing_card
        return {
            "viewport": scroll.viewport().height(),
            "content": w._stage_widget.height(),
            "value": bar.value(),
            "maximum": bar.maximum(),
            "card_top": card.y(),
            "card_bottom": card.y() + card.height(),
        }

    def _below_fold(self, geometry):
        """Px of the card below the viewport bottom (<= 0: fully
        visible)."""
        return (geometry["card_bottom"]
                - (geometry["value"] + geometry["viewport"]))

    def _expand(self):
        self.window.btn_briefing_expand.click()
        _pump(self.app, 0.8)

    def _collapse(self):
        self.window.btn_briefing_expand.click()
        _pump(self.app, 0.5)

    def test_no_scroll_on_first_paint(self):
        # The fix fires on the expand tap only — showing the
        # window must never move the stage bar.
        for width, height in ((1280, 800), (1024, 768)):
            self._show_at(width, height)
            self.assertEqual(
                self.window._stage_scroll.verticalScrollBar().value(),
                0, f"stage scrolled at first paint at {width}x{height}")

    def test_expand_scrolls_card_fully_into_view_1280(self):
        # The finding's own geometry: 551 px viewport, 610 px of
        # content, the 140 px card 59 px below the fold pre-fix.
        self._show_at(1280, 800)
        self.assertFalse(self.window._briefing_expanded)
        self._expand()
        self.assertTrue(self.window._briefing_expanded)
        geometry = self._geometry()
        self.assertGreater(geometry["value"], 0,
                           f"bar never moved on expand: {geometry}")
        self.assertEqual(geometry["value"], geometry["maximum"],
                         f"bar did not scroll as far as the card needs: "
                         f"{geometry}")
        self.assertLessEqual(
            self._below_fold(geometry), 0,
            f"card still below the fold after expand: {geometry}")

    def test_expand_scrolls_card_fully_into_view_1024(self):
        # The narrower card wraps the schedule to two lines (157 px
        # card, 644 px content, 519 px viewport) — the scroll must
        # still bring the whole card in, at the scrollbar maximum.
        self._show_at(1024, 768)
        self._expand()
        geometry = self._geometry()
        self.assertGreater(geometry["value"], 0,
                           f"bar never moved on expand: {geometry}")
        self.assertEqual(geometry["value"], geometry["maximum"],
                         f"bar did not scroll to the maximum: {geometry}")
        self.assertLessEqual(
            self._below_fold(geometry), 0,
            f"card still below the fold after expand: {geometry}")

    def test_collapse_does_not_scroll_down(self):
        # Collapse schedules no scroll: the bar never moves down
        # on the collapse tap (at 1280 the content fits again, so
        # Qt clamps it back to 0).
        self._show_at(1280, 800)
        self._expand()
        expanded = self._geometry()
        self.assertGreater(expanded["value"], 0)
        self._collapse()
        self.assertFalse(self.window._briefing_expanded)
        collapsed = self._geometry()
        self.assertLessEqual(collapsed["value"], expanded["value"],
                             f"bar moved down on collapse: {collapsed}")
        self.assertEqual(collapsed["value"], 0,
                         f"bar did not return to 0: {collapsed}")

    def test_reexpand_scrolls_again(self):
        # The scroll fires once per tap, not once ever: collapse
        # then expand again and the card comes fully into view
        # a second time.
        self._show_at(1280, 800)
        self._expand()
        self._collapse()
        self._expand()
        geometry = self._geometry()
        self.assertEqual(geometry["value"], geometry["maximum"],
                         f"re-expand did not scroll: {geometry}")
        self.assertLessEqual(
            self._below_fold(geometry), 0,
            f"card still below the fold after re-expand: {geometry}")
