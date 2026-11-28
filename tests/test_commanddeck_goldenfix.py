"""Command Deck golden-defect fixes (D1-D8, 2026-11-01).

Regression pins for the Phase A golden fixes. The GUI modules import
the frozen ``voice_assistant.*`` package names and cannot be imported
here, so structure is pinned by AST over the real files — the tree's
established pattern (uis1, Phase E) — and the one standalone-importable
behaviour (Pulse value tracking) is proved with real offscreen widgets,
skipped where PySide6 is absent. Rendered appearance is proved by the
goldenfix before/after shots, not by pretending a source scan is a
render.

What each pin guards (root cause in the comment beside it):
* D6: the Scheduled tab's scroll content painted palette-Window light
  because setWidget() flips autoFillBackground on.
* D1: the Home stage crushed below its minimum at short heights;
  fixed by scrolling the stage (composition untouched), the tier
  floor carried onto the scroller, and the setup banner wrapping.
  D1 residual: the header pills row clipped mid-glyph at
  908x500@120; fixed by a flow layout — chips wrap whole to a
  second line only when they do not fit, never clipped.
* D2/D3/D4: Memory / Skills pages crushed below their minimums at
  short heights; fixed by the Doctor/Pulse outer-scroll wrapper.
* D5: the sidebar column crushed below its minimum at short heights;
  fixed by scrolling the column inside its fixed 220 px frame.
* D7: the plain confirm fallback's detail label clipped its first
  line when the dialog landed narrower than its sizeHint width;
  fixed by top alignment + a sizeHint minimum width (plain only).
* D8: the Pulse value clipped at the card edge when the live value
  outgrew its grid column; fixed by tracking minimum width (never
  truncating). The Globe fallback had no scroll area; fixed by
  wrapping the fallback text. The Senses radar hint is NOT pinned as
  fixed: at 908x500@120 it straddles the fold of a scrolling page
  (reachable by scrolling), the same class the golden index exempts
  as normal scroll behaviour for lists — see the Senses test note.
"""

import ast
import sys
import types
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent
GUI = TREE / "gui"


# T-3 (debug pass): gui modules import the frozen voice_assistant.*
# package names (gui/skills_galaxy_widget.py does `from
# voice_assistant.core.gui_facade import ...`) — the alias is
# registered at runtime by doctor._register_package(), so tests
# register it themselves. Snapshot/restore discipline (L3):
# register only keys that are absent, and tearDownModule removes
# exactly the keys this module added — never delete-all. (The
# galaxy test below installs its own pathless stubs with
# setdefault, so it never overwrites these real packages.)
_ADDED_VA_MODULES = []


def setUpModule():
    for key, path in (
        ("voice_assistant", str(TREE)),
        ("voice_assistant.core", str(TREE / "core")),
        ("voice_assistant.gui", str(TREE / "gui")),
    ):
        if key not in sys.modules:
            pkg = types.ModuleType(key)
            pkg.__path__ = [path]
            sys.modules[key] = pkg
            _ADDED_VA_MODULES.append(key)


def tearDownModule():
    for key in _ADDED_VA_MODULES:
        sys.modules.pop(key, None)
    _ADDED_VA_MODULES.clear()


def _source(rel):
    return (GUI / rel).read_text(encoding="utf-8")


def _func(tree_text, name):
    tree = ast.parse(tree_text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found")


def _func_src(rel, name):
    text = _source(rel)
    return ast.get_source_segment(text, _func(text, name))


def _order(src, markers):
    positions = [src.index(m) for m in markers]
    assert positions == sorted(positions), (
        f"markers out of order: {list(zip(markers, positions))}")
    return positions


class D6Tests(unittest.TestCase):
    def test_reminders_scroll_resets_autofill(self):
        # GUI split #8 INTENTIONAL: _build_reminders_page moved verbatim to
        # window_pages_activity.py. Assertion unchanged.
        src = _func_src("window_pages_activity.py", "_build_reminders_page")
        _order(src, [
            "self.reminders_scroll.setWidget(_reminders_content)",
            "_reminders_content.setAutoFillBackground(False)",
            "self.reminders_scroll.viewport().setAutoFillBackground(False)",
        ])


class D1Tests(unittest.TestCase):
    def test_stage_scrolls_instead_of_crushing(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        src = _func_src("window_shell_assistant.py", "_build_assistant_page")
        self.assertIn("self._stage_scroll = QScrollArea()", src)
        self.assertIn("self._stage_scroll.setWidget(stage)", src)
        self.assertIn("split.addWidget(self._stage_scroll)", src)
        self.assertNotIn("split.addWidget(stage)", src)
        self.assertIn("stage.setAutoFillBackground(False)", src)
        self.assertIn(
            "self._stage_scroll.viewport().setAutoFillBackground(False)",
            src)

    def test_tier_floor_carried_onto_scroller(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        src = _func_src("window_shell_frame.py", "_apply_breakpoint_tier")
        self.assertIn("stage.setMinimumWidth(stage_floor_for(width))", src)
        self.assertIn("scroll.setMinimumWidth(stage_floor_for(width))", src)

    def test_home_composition_markers_unchanged(self):
        # Overflow handling only: the settled Phase E home keeps its
        # docked chat panel, splitter pin, briefing card and pace
        # row; no drawer, rail or hero redesign rides along.
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        src = _func_src("window_shell_assistant.py", "_build_assistant_page")
        for marker in ("split.setSizes([1000, 400])",
                       "stage_layout.addWidget(self.briefing_card)",
                       "stage_layout.addWidget(self.pace_box)",
                       "self.chat_panel"):
            self.assertIn(marker, src)

    def test_setup_banner_wraps_instead_of_clipping(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        src = _func_src("window_shell_sidebar.py", "_build_setup_banner")
        _order(src, [
            'self.setup_banner_label = QLabel("Setting VYUK up…")',
            "self.setup_banner_label.setWordWrap(True)",
        ])


class D2D3D4Tests(unittest.TestCase):
    def test_memory_page_wrapped_in_outer_scroll(self):
        # GUI split #8 INTENTIONAL: _build_memory_page moved verbatim to
        # window_pages_memory.py. Phase 1: the List/Web toggle is
        # gone — the saved memory/view migrates once ("web" lands
        # on the Knowledge Graph tab) and the key is retired; the
        # outer scroll and the list card are unchanged.
        src = _func_src("window_pages_memory.py", "_build_memory_page")
        _order(src, [
            'initial = 4 if saved_view == "web"',
            "scroll = QScrollArea()",
            "scroll.setWidget(page)",
            "page.setAutoFillBackground(False)",
        ])
        self.assertIn("self.memory_list = QListWidget()", src)
        self.assertIn("layout.addWidget(card, stretch=1)", src)
        self.assertIn('qsettings_remove("memory/view"', src)

    def test_skills_page_wrapped_in_outer_scroll(self):
        # GUI split #8 INTENTIONAL: _build_skills_page moved verbatim to
        # window_pages_memory.py. Assertion unchanged.
        # UI Phase 2 (2026-11-25): the page is a left column beside
        # the detail drawer, so the rescan button now lands on the
        # left column's layout — the outer scroll wrapping is
        # unchanged.
        src = _func_src("window_pages_memory.py", "_build_skills_page")
        _order(src, [
            "left.addWidget(btn_rescan)",
            "scroll = QScrollArea()",
            "scroll.setWidget(page)",
            "page.setAutoFillBackground(False)",
        ])
        self.assertIn("self.plugin_list = QListWidget()", src)

    def test_galaxy_canvas_floor_preserved(self):
        # The D4 fix is the page scroll plus the fit guard below,
        # not a smaller canvas: the canvas minimum that the ring
        # needs stays 320x360 — the Galaxy looks-round fix
        # (2026-11-25, parent-verified) raised the floor from 280
        # to 360 so the canvas is the dominant element instead of
        # a small strip; this pin still guards that floor.
        # GUI split #5 INTENTIONAL: canvas moved verbatim to
        # skills_galaxy_canvas2d.py. Assertion unchanged.
        src = _source("skills_galaxy_canvas2d.py")
        self.assertIn("self.setMinimumSize(320, 360)", src)


class D4Tests(unittest.TestCase):
    def test_fit_guard_keys_on_layout_not_minimum_size(self):
        # Root cause of the still-collapsed ring at 908/980:
        # _fit_view refused to fit while the widget sat at its
        # minimum size — which is also its legitimate final size
        # at small windows — so the ring painted at zoom 1.0,
        # far larger than the widget, showing only top-edge dots.
        # The guard must key on a layout pass having run.
        # GUI split #5 INTENTIONAL: canvas moved verbatim to
        # skills_galaxy_canvas2d.py. Assertions unchanged.
        src = _func_src("skills_galaxy_canvas2d.py", "_fit_view")
        self.assertIn("self._seen_resize", src)
        self.assertNotIn("minimumWidth() + 4", src)
        self.assertNotIn("minimumHeight() + 4", src)
        resize_src = _func_src("skills_galaxy_canvas2d.py", "resizeEvent")
        _order(resize_src, [
            "self._seen_resize = True",
            "self._fit_view()",
        ])

    def test_galaxy_fits_at_minimum_size(self):
        # Real-widget proof (skipped without PySide6): at the old
        # failing size (326x280, at the canvas minimum) the fit
        # runs and frames the ring, and data loaded before first
        # show still defers to the coming resize.
        try:
            import PySide6  # noqa: F401
        except ImportError:
            self.skipTest("PySide6 not installed on this interpreter")
        import os
        import sys
        import types
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        catalog = types.ModuleType(
            "voice_assistant.gui.skills_catalog")
        catalog.GROUP_ORDER = ["Hands & eyes", "Mind"]
        catalog.group_for_name = lambda name: (
            "Hands & eyes" if name.startswith("hands_") else "Mind")
        stubs = (
            ("voice_assistant", types.ModuleType("voice_assistant")),
            ("voice_assistant.gui",
             types.ModuleType("voice_assistant.gui")),
            ("voice_assistant.gui.skills_catalog", catalog))
        # Suite discipline (same as test_uiinteraction): a test
        # that installs voice_assistant.* stubs MUST remove exactly
        # the ones it added. A pathless voice_assistant.gui stub
        # left behind breaks every later
        # `from voice_assistant.gui.X import ...` in the same
        # process with "'voice_assistant.gui' is not a package"
        # — that pollution caused 8 uiinteraction errors in the
        # first full run20 before this cleanup existed.
        added = [name for name, _mod in stubs
                 if name not in sys.modules]
        for name, mod in stubs:
            sys.modules.setdefault(name, mod)
        try:
            from gui.skills_galaxy import Galaxy2DCanvas, build_galaxy
            data = build_galaxy([
                {"name": "hands_a", "description": "a"},
                {"name": "hands_b", "description": "b"},
                {"name": "mind_a", "description": "a"},
                {"name": "mind_b", "description": "b"},
            ], {})
            canvas = Galaxy2DCanvas()
            canvas.set_data(data)
            # Pre-show: layout has not run, fit stays pending.
            self.assertFalse(canvas._fitted)
            canvas.resize(326, 280)
            canvas.show()
            app.processEvents()
            self.assertTrue(canvas._fitted)
            # Framed, not the unfitted zoom 1.0 that overflowed
            # the widget and showed only top-edge dots.
            self.assertLess(canvas.zoom_factor, 1.0)
            self.assertGreaterEqual(canvas.zoom_factor, 0.25)
            canvas._pulse_timer.stop()
            canvas.deleteLater()
            app.processEvents()
        finally:
            for name in added:
                sys.modules.pop(name, None)
        del app


class D5Tests(unittest.TestCase):
    def test_sidebar_scrolls_inside_fixed_frame(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        src = _func_src("window_shell_sidebar.py", "_build_sidebar")
        self.assertIn("sidebar.setFixedWidth(220)", src)
        _order(src, [
            "sidebar_scroll = QScrollArea()",
            "sidebar_scroll.setWidget(inner)",
            "inner.setAutoFillBackground(False)",
            "sidebar_scroll.viewport().setAutoFillBackground(False)",
        ])

    def test_sidebar_order_markers_unchanged(self):
        # Restore 2026-11-01 (owner-delegated): the uis1 order
        # contract is re-pinned to the §9.2 section labels —
        # same buttons, same order, same 220 px frame; only the
        # labels changed (SYSTEM replaces Advanced / Explore).
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        src = _func_src("window_shell_sidebar.py", "_build_sidebar")
        _order(src, [
            '_core_label = QLabel("CORE")',
            "layout.addWidget(self.nav_buttons[0])",
            '_mission_label = QLabel("MISSION")',
            "layout.addWidget(self.nav_buttons[1])",
            '_archive_label = QLabel("ARCHIVE")',
            "layout.addWidget(self.nav_buttons[2])",
            # UI Phase 2 (2026-11-25): CAPABILITIES for Skills.
            '_capabilities_label = QLabel("CAPABILITIES")',
            "layout.addWidget(self.nav_buttons[3])",
            '_system_label = QLabel("SYSTEM")',
            "for btn in self.nav_buttons[4:7]:",
            # Stage 0 features batch 1 (Group D): the VIEWS
            # section sits between SYSTEM and the stretch.
            # INTENTIONAL UPDATE (UI Phase 3, 2026-11-25): the
            # section is WORKSPACES now.
            # INTENTIONAL UPDATE (UI Phase 5, 2026-11-27): seven
            # workspace buttons (nav_buttons[7:14]); Settings is
            # the footer at nav_buttons[14].
            '_views_label = QLabel("WORKSPACES")',
            "for btn in self.nav_buttons[7:14]:",
            "layout.addStretch()",
            "layout.addWidget(self.nav_buttons[14])",
        ])


class D7Tests(unittest.TestCase):
    def test_plain_detail_top_aligned_and_min_width(self):
        # GUI split #10 batch 1 INTENTIONAL: confirm dialogs moved
        # verbatim to window_services_confirm.py. Assertion unchanged.
        src = _func_src("window_services_confirm.py",
                        "_build_plain_confirm_dialog")
        self.assertIn("detail.setAlignment(", src)
        self.assertIn("Qt.AlignmentFlag.AlignTop", src)
        self.assertIn(
            "dlg.setMinimumWidth(min(dlg.sizeHint().width(), 720))", src)

    def test_designed_confirm_untouched_by_plain_fix(self):
        # The fix is scoped to the plain fallback: the designed
        # HudDialog confirm (which never showed the defect) keeps
        # its own sizing and alignment.
        # GUI split #10 batch 1 INTENTIONAL: _build_confirm_dialog
        # moved verbatim to window_services_confirm.py.
        src = _func_src("window_services_confirm.py", "_build_confirm_dialog")
        self.assertNotIn("AlignTop", src)
        self.assertNotIn("setMinimumWidth(min(", src)


class D8Tests(unittest.TestCase):
    def test_pulse_value_tracks_its_width(self):
        src = _source("pulse_page.py")
        self.assertIn("def set_value(self, text: str)", src)
        self.assertIn("self.value_label.setMinimumWidth(0)", src)
        self.assertIn(
            "self.value_label.setMinimumWidth(\n"
            "            self.value_label.sizeHint().width())", src)
        # Every live update goes through the tracker; no direct
        # setText on the row value label remains outside it.
        # (Build 6: cards became rows, so the local is `row` now.)
        self.assertEqual(src.count("row.value_label.setText("), 0)
        self.assertGreater(src.count("row.set_value("), 0)


    def test_senses_page_scroll_shell_exists(self):
        # NOT a fix pin: the radar hint at 908x500@120 straddles
        # the fold of this scrolling page (content taller than
        # the viewport, reachable by scrolling) — the same class
        # the golden index exempts as normal scroll behaviour.
        # This pin only records that the scroll shell exists, so
        # the hint is reachable; it must not be cited as proof
        # the first-paint straddle is gone.
        src = _source("senses_page.py")
        self.assertIn("scroll = QScrollArea()", src)
        self.assertIn("scroll.setWidget(content)", src)


class D8PulseWidgetTests(unittest.TestCase):
    """Real-widget proof for set_value (skipped without PySide6)."""

    def test_set_value_tracks_both_directions(self):
        try:
            import PySide6  # noqa: F401
        except ImportError:
            self.skipTest("PySide6 not installed on this interpreter")
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from gui.pulse_page import _BarCard
        card = _BarCard("net", "Network", "#35D6FF")
        card.set_value("499.1 KB/s")
        self.assertEqual(card.value_label.text(), "499.1 KB/s")
        self.assertEqual(card.value_label.minimumWidth(),
                         card.value_label.sizeHint().width())
        long_width = card.value_label.minimumWidth()
        self.assertGreater(long_width, 0)
        card.set_value("—")
        self.assertEqual(card.value_label.text(), "—")
        self.assertEqual(card.value_label.minimumWidth(),
                         card.value_label.sizeHint().width())
        # Shorter text gives the width back (no ratchet to the
        # widest value ever seen).
        self.assertLess(card.value_label.minimumWidth(), long_width)
        del app


class D1WrapTests(unittest.TestCase):
    """D1 residual: header pills wrap instead of clipping.

    Real-widget proof (skipped without PySide6): the StatusStrip's
    chips ride a flow layout, so at a width that fits every chip
    the strip is one row, and at a narrow width the chips wrap
    whole to further rows — never clipped mid-glyph — while chip
    order and texts are unchanged. The strip grows its own
    minimum height from its width (resizeEvent), so the parent
    header row grows instead of squeezing the row.
    """

    def _shown_strip(self, app, width):
        from gui import state_bus
        from gui.design_system.statusstrip import StatusStrip
        strip = StatusStrip(bus=state_bus.StateBus())
        strip.status_label.setText("LISTENING")
        # UI rethink Build 1: no mode chip — the wake chip is the
        # third visible chip in this wrap exercise now.
        strip.wake_label.setText("WAKE ON")
        strip.wake_label.setVisible(True)
        strip.mic_label.setText("MIC OFF")
        strip.mic_label.setVisible(True)
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(strip)
        host.setFixedWidth(width)
        host.show()
        app.processEvents()
        app.processEvents()
        return host, strip

    def _glyph_safe_width(self, chip):
        # Mirror of flowlayout._shrink_allowance: a chip may shrink
        # by up to its right padding share without clipping glyphs.
        text = chip.text()
        if not text:
            return chip.sizeHint().width()
        text_width = chip.fontMetrics().horizontalAdvance(text)
        return chip.sizeHint().width() - max(
            0, (chip.sizeHint().width() - text_width) // 2)

    def test_wraps_only_on_overflow_and_never_clips(self):
        try:
            import PySide6  # noqa: F401
        except ImportError:
            self.skipTest("PySide6 not installed on this interpreter")
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QWidget
        app = QApplication.instance() or QApplication([])
        try:
            # Wide: one row, full sizeHints.
            host_wide, wide = self._shown_strip(app, 900)
            try:
                chips_wide = [wide.glyph_label, wide.status_label,
                              wide.wake_label, wide.mic_label]
                self.assertEqual(
                    len({c.y() for c in chips_wide}), 1)
                wide_height = wide.height()
                for chip in chips_wide:
                    self.assertEqual(
                        chip.width(), chip.sizeHint().width())
            finally:
                host_wide.close()
                app.processEvents()
            # Within padding tolerance (~12 px squeeze, the old
            # 908x500@100 case): still one row, chips shrunk into
            # right padding only, glyphs intact.
            host_tol, tol = self._shown_strip(app, 300)
            try:
                chips_tol = [tol.glyph_label, tol.status_label,
                             tol.wake_label, tol.mic_label]
                self.assertEqual(len({c.y() for c in chips_tol}), 1)
                for chip in chips_tol:
                    self.assertGreaterEqual(
                        chip.width(), self._glyph_safe_width(chip))
                    self.assertLessEqual(
                        chip.y() + chip.height(), tol.height())
            finally:
                host_tol.close()
                app.processEvents()
            # Beyond tolerance: wraps whole chips, never clips.
            host_narrow, narrow = self._shown_strip(app, 200)
            try:
                chips = [narrow.glyph_label, narrow.status_label,
                         narrow.wake_label, narrow.mic_label]
                self.assertGreater(
                    len({c.y() for c in chips}), 1)
                self.assertGreater(narrow.height(), wide_height)
                for chip in chips:
                    self.assertGreaterEqual(
                        chip.width(), self._glyph_safe_width(chip))
                    self.assertLessEqual(
                        chip.y() + chip.height(), narrow.height())
            finally:
                host_narrow.close()
                app.processEvents()
        finally:
            del app

    def test_chip_order_and_content_unchanged(self):
        try:
            import PySide6  # noqa: F401
        except ImportError:
            self.skipTest("PySide6 not installed on this interpreter")
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        try:
            host, strip = self._shown_strip(app, 900)
            try:
                layout = strip.layout()
                ordered = [layout.itemAt(i).widget()
                           for i in range(layout.count())]
                self.assertEqual(
                    ordered,
                    [strip.glyph_label, strip.status_label,
                     strip.watch_label, strip.capture_label,
                     strip.mic_label, strip.wake_label])
                self.assertEqual(strip.status_label.text(), "LISTENING")
                self.assertEqual(strip.wake_label.text(), "WAKE ON")
                self.assertEqual(strip.mic_label.text(), "MIC OFF")
            finally:
                host.close()
                app.processEvents()
        finally:
            del app

    def test_strip_uses_flow_layout(self):
        src = _source("design_system/statusstrip.py")
        self.assertIn("FlowLayout", src)
        self.assertIn("heightForWidth", src)
        self.assertIn("setMinimumHeight", src)
        self.assertNotIn("QHBoxLayout", src)
