"""Round-3 section E, E-F205: Settings has a horizontal scrollbar
at the 908 minimum width.

Section-E finding E-F205: Settings at the 908 window minimum is 72 px
wider than its viewport (viewport 624 px, content 696 px,
probe-measured hmax=72) — the user must scroll right to see the
right edge of every card. Measured drivers, all the same shape (a
non-wrapping QHBoxLayout of buttons whose summed minimum widths
exceed the viewport):
  * Appearance card: the 11-swatch row, 11x46 px fixed + Custom
    (row min 660, card min 692) — gui/window_settings_voice.py
  * Sounds card: the Kenney audition row, 6 buttons
    (row min 600, card min 632) — gui/window_settings_voice.py
  * System status card (Advanced drawer): the second button row,
    5 buttons (row min 666, card min 698) —
    gui/window_settings_advcards.py
Absent at 1024+ (all three rows fit on one line there).

The fix (E-F205, two product files): each of the three rows uses
the shared wrapping FlowLayout (gui/design_system/flowlayout.py)
instead of QHBoxLayout — single-row equivalent at wide widths
(same spacing, same order, same buttons, same handlers), wrapping
to a second line at 908. No button shrinks (FlowLayout gives
QPushButtons zero shrink allowance — they wrap whole), no
behaviour changes, no new setStyleSheet calls, no font literals.

GUI tests are Qt-only (the test_chat_ui WindowBase shape: the
real MainWindow offscreen), gated the same way as the E-F202/E-F203
suites (skipped on the sys interpreter, which has no PySide6).
Three source pins run on both interpreters: each row is a
FlowLayout in the product file, so the rows cannot silently go
back to a non-wrapping hbox.
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
    from tests.test_chat_ui import WindowBase
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    WindowBase = unittest.TestCase  # type: ignore[assignment]


def _pump(app, secs=0.4):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


class TestRowsAreFlowLayouts(unittest.TestCase):
    """Source pins (both interpreters): the three E-F205 rows are
    wrapping FlowLayouts, not non-wrapping hboxes."""

    def test_swatch_row_is_flowlayout(self):
        src = (ROOT / "gui" / "window_settings_voice.py").read_text(
            encoding="utf-8")
        self.assertIn("swatch_row = FlowLayout(spacing=6)", src)
        self.assertNotIn("swatch_row = QHBoxLayout()", src)
        self.assertIn("E-F205", src)

    def test_audition_row_is_flowlayout(self):
        src = (ROOT / "gui" / "window_settings_voice.py").read_text(
            encoding="utf-8")
        self.assertIn("aud_row = FlowLayout(spacing=4)", src)
        self.assertNotIn("aud_row = QHBoxLayout()", src)

    def test_trust_row2_is_flowlayout(self):
        src = (ROOT / "gui" / "window_settings_advcards.py").read_text(
            encoding="utf-8")
        self.assertIn("trust_row2 = FlowLayout(spacing=6)", src)
        self.assertNotIn("trust_row2 = QHBoxLayout()", src)


class _SettingsBase(WindowBase):
    """Real MainWindow offscreen, Settings page, at a chosen width."""

    def _show_settings(self, width=908):
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — overlay is best-effort
            pass
        _pump(self.app, 0.4)
        self.window._show_page(4)  # Settings page
        _pump(self.app, 0.6)
        self.window.resize(width, 750)
        _pump(self.app, 0.6)

    def _measure(self):
        scroll = self.window.settings_scroll
        return {
            "hmax": scroll.horizontalScrollBar().maximum(),
            "viewport": scroll.viewport().width(),
            "content_min":
                self.window.settings_content.minimumSizeHint().width(),
        }

    def _row_buttons(self):
        return [
            ("swatches", list(self.window.theme_buttons.values())),
            ("audition", list(self.window.audition_buttons.values())),
            ("trust2", [
                self.window.btn_unified_activity,
                self.window.btn_cost_ledger,
                self.window.btn_today,
                self.window.btn_templates,
                self.window.btn_memory_panel,
            ]),
        ]

    def _expand_advanced(self):
        self.window._on_more_toggled()
        _pump(self.app, 0.8)


@unittest.skipUnless(_QT, "Qt-only (no PySide6 on sys)")
class TestNoHorizontalScrollbar(_SettingsBase):
    """E-F205: no horizontal scrollbar on Settings at any width,
    collapsed or with Advanced expanded."""

    def test_no_hscroll_at_all_widths(self):
        self._show_settings()
        for width in (908, 1024, 1280, 1920):
            with self.subTest(width=width):
                self.window.resize(width, 750)
                _pump(self.app, 0.6)
                m = self._measure()
                self.assertEqual(
                    m["hmax"], 0,
                    f"hmax={m['hmax']} viewport={m['viewport']} "
                    f"content_min={m['content_min']} at {width}")

    def test_no_hscroll_908_expanded(self):
        self._show_settings(908)
        self._expand_advanced()
        m = self._measure()
        self.assertEqual(
            m["hmax"], 0,
            f"hmax={m['hmax']} viewport={m['viewport']} "
            f"content_min={m['content_min']} with Advanced open")

    def test_groups_minimums_fit_908(self):
        self._show_settings(908)
        m = self._measure()
        for hdr, box in self.window._settings_group_headers.items():
            with self.subTest(group=hdr.text()):
                self.assertLessEqual(
                    box.minimumSizeHint().width(), m["viewport"],
                    f"{hdr.text()} min="
                    f"{box.minimumSizeHint().width()} "
                    f"viewport={m['viewport']}")


@unittest.skipUnless(_QT, "Qt-only (no PySide6 on sys)")
class TestRowsStayUsable(_SettingsBase):
    """E-F205: the wrapped rows keep every button reachable and
    whole, and stay a single row where they fit."""

    def test_row_buttons_visible_and_inside_viewport_908(self):
        from PySide6.QtCore import QPoint
        self._show_settings(908)
        scroll = self.window.settings_scroll
        vp = scroll.viewport()
        vw = vp.width()
        # Collapsed: swatches + audition are on the page.
        for label, btns in self._row_buttons():
            if label == "trust2":
                continue
            for btn in btns:
                with self.subTest(row=label, button=btn.text()):
                    self.assertTrue(btn.isVisible())
                    right = btn.mapTo(vp, QPoint(btn.width(), 0)).x()
                    self.assertLessEqual(right, vw)
        # Expanded: the trust row joins them.
        self._expand_advanced()
        for _label, btns in self._row_buttons():
            for btn in btns:
                with self.subTest(row="expanded", button=btn.text()):
                    self.assertTrue(btn.isVisible())
                    right = btn.mapTo(vp, QPoint(btn.width(), 0)).x()
                    self.assertLessEqual(right, vw)

    def test_rows_single_line_at_1024(self):
        self._show_settings(1024)
        self._expand_advanced()
        for label, btns in self._row_buttons():
            with self.subTest(row=label):
                ys = sorted({b.geometry().y() for b in btns
                             if b.isVisible()})
                self.assertTrue(ys, f"{label}: no visible buttons")
                self.assertEqual(
                    max(ys) - min(ys), 0,
                    f"{label} wraps at 1024: ys={ys}")

    def test_swatch_selection_still_works(self):
        self._show_settings(908)
        sunset = self.window.theme_buttons["Sunset"]
        cyan = self.window.theme_buttons["Cyan"]
        sunset.click()
        _pump(self.app, 0.5)
        self.assertTrue(sunset.isChecked())
        self.assertEqual(self.window.theme_name_label.text(), "Sunset")
        cyan.click()
        _pump(self.app, 0.5)
        self.assertTrue(cyan.isChecked())
        self.assertEqual(self.window.theme_name_label.text(), "Cyan")

    def test_audition_and_trust_buttons_enabled(self):
        self._show_settings(908)
        for btn in self.window.audition_buttons.values():
            with self.subTest(button=btn.text()):
                self.assertTrue(btn.isEnabled())
        self._expand_advanced()
        for btn in (self.window.btn_unified_activity,
                    self.window.btn_cost_ledger, self.window.btn_today,
                    self.window.btn_templates, self.window.btn_memory_panel):
            with self.subTest(button=btn.text()):
                self.assertTrue(btn.isEnabled())
                self.assertTrue(btn.isVisible())
