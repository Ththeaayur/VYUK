"""Round-3 section E, E-F305: the plan-approval list is a fixed
160 px box, ~100 px dead space with 2 steps (minor, cosmetic).

Filed shape (section E, slice 3): plan approval with 2 approvable
steps ("Tidy up before the call") — plan_list.setMaximumHeight(160)
(gui/plan_approval_dialog.py:95-96) regardless of content, leaving
~100 px of dead space between the checked steps and "Still asks
one by one:". Render: r3e_screens_work/renders/slice3/
confirm_plan_1280_day.png.

THE FIX (1 file, gui/plan_approval_dialog.py): the list sizes to
its content, capped at the old 160 px. Measured on the real dialog
offscreen (repro_f305.py): row height is 17 px, so content is
17n + 2 (frame); dead space was 141 px (1 step), 124 px (2 steps),
73 px (5 steps); long plans already scrolled (n=10: content 172,
scrollbar visible, vbar_max=1). After the fix the list is exactly
its content height for short plans (2-step dialog 527 -> 403 px)
and stays at the 160 px cap with the scrollbar for long plans.
The dialog itself has no fixed height (HudDialog sizes to
content), so the list height was never load-bearing. No restyle,
no other dialog touched; the "Ask me each time" safe default,
the checkboxes and the still-individual section are unchanged.

GUI tests are Qt-only (the real dialog offscreen), gated the same
way as the E-F202/E-F203/E-F205/E-F206/E-F207/E-F208/E-F209/E-F210/
E-F304 suites (skipped on the sys interpreter, which has no
PySide6). Source pins run on both interpreters. The dialog builds
standalone (parent=None), like test_perm_popups does — no
MainWindow boot, and the repro/suite touch no data.
"""

import os
import pathlib
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import PySide6  # noqa: F401 — availability probe only
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False

DIALOG = ROOT / "gui" / "plan_approval_dialog.py"


class TestFixInSource(unittest.TestCase):
    """The fix is present in the product source — pins run on
    both interpreters."""

    def test_fixed_160_box_is_gone(self):
        src = DIALOG.read_text(encoding="utf-8")
        self.assertNotIn("plan_list.setMaximumHeight(160)", src)
        self.assertIn("plan_list.setFixedHeight(min(160, content_h))",
                      src)

    def test_content_height_comes_from_the_rows(self):
        src = DIALOG.read_text(encoding="utf-8")
        self.assertIn("plan_list.sizeHintForRow(i)", src)
        self.assertIn("2 * plan_list.frameWidth()", src)

    def test_branch_comment_names_the_finding(self):
        src = DIALOG.read_text(encoding="utf-8")
        self.assertIn("E-F305", src)
        self.assertIn("124 px", src)

    def test_cap_and_surrounding_dialog_unchanged(self):
        src = DIALOG.read_text(encoding="utf-8")
        # The 160 px cap survives (long plans scroll as before).
        self.assertIn("min(160, content_h)", src)
        # Still-individual section, no-ask line, safe default.
        self.assertIn('"Still asks one by one:"', src)
        self.assertIn("more step(s) never ask at all.", src)
        self.assertIn("btn_ask.setDefault(True)", src)


def _preview(n, still=1):
    return {
        "approvable": [
            {"index": i, "tool": "open_app",
             "label": f"Step {i + 1}",
             "summary": f"app=App{i + 1}"}
            for i in range(n)
        ],
        "still_individual": [
            {"index": 99, "tool": "delete_file",
             "label": "Delete old draft", "summary": "",
             "reason": "deleting always asks"},
        ] if still else [],
        "no_ask": 1 if still else 0,
    }


@unittest.skipUnless(_QT, "Qt-only: the real dialog offscreen")
class TestPlanListHeight(unittest.TestCase):
    """The list sizes to its content; long plans still scroll."""

    def setUp(self):
        from PySide6.QtWidgets import QApplication  # noqa: E402

        self.app = QApplication.instance() or QApplication([])
        from gui.plan_approval_dialog import (  # noqa: E402
            approved_indices, build_plan_approval_dialog)
        self._build = build_plan_approval_dialog
        self._approved_indices = approved_indices

    def _dialog(self, n):
        dlg = self._build(None, "Tidy up before the call", _preview(n))
        dlg.show()
        self.app.processEvents()
        return dlg

    @staticmethod
    def _content_h(lst):
        return (sum(lst.sizeHintForRow(i) for i in range(lst.count()))
                + 2 * lst.frameWidth())

    def _assert_fits(self, n):
        dlg = self._dialog(n)
        try:
            lst = dlg._plan_list
            self.assertEqual(lst.height(), self._content_h(lst))
            self.assertLess(lst.height(), 160)
        finally:
            dlg.close()

    def test_two_step_list_has_no_dead_space(self):
        self._assert_fits(2)

    def test_one_and_five_step_lists_fit_content(self):
        self._assert_fits(1)
        self._assert_fits(5)

    def test_long_plans_stay_capped_and_scroll(self):
        for n in (10, 20):
            dlg = self._dialog(n)
            try:
                lst = dlg._plan_list
                self.assertEqual(lst.height(), 160)
                vbar = lst.verticalScrollBar()
                self.assertTrue(vbar.isVisible())
                self.assertGreaterEqual(vbar.maximum(), 1)
                lst.scrollToBottom()
                self.app.processEvents()
                last = lst.item(lst.count() - 1)
                rect = lst.visualItemRect(last)
                self.assertGreaterEqual(rect.top(), 0)
                self.assertLessEqual(rect.bottom(),
                                     lst.viewport().height())
            finally:
                dlg.close()

    def test_short_plan_shrinks_the_dialog_buttons_intact(self):
        dlg2 = self._dialog(2)
        try:
            h2 = dlg2.height()
            self.assertTrue(dlg2._plan_btn_approve.isVisible())
            self.assertTrue(dlg2._plan_btn_ask_each.isVisible())
            self.assertTrue(dlg2._plan_btn_ask_each.isDefault())
            self.assertFalse(dlg2._plan_btn_approve.isDefault())
            self.assertEqual(self._approved_indices(dlg2), [0, 1])
        finally:
            dlg2.close()
        dlg10 = self._dialog(10)
        try:
            self.assertLess(h2, dlg10.height())
        finally:
            dlg10.close()

    def test_no_approvable_steps_still_builds_without_a_list(self):
        dlg = self._build(None, "Tidy up", _preview(0, still=1))
        try:
            dlg.show()
            self.app.processEvents()
            self.assertIsNone(dlg._plan_list)
            self.assertTrue(dlg._plan_btn_approve.isVisible())
        finally:
            dlg.close()
