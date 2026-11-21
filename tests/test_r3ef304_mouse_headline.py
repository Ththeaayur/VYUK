"""Round-3 section E, E-F304: the confirm headline for mouse_control
falls back to the generic 'VYUK wants to run "Mouse".' (minor, copy).

Filed shape (section E, slice 3): S-1 confirm dialog for
mouse_control, action=click, x=640, y=400 -> the headline names
the tool instead of the consequence.

THE FIX (1 file, gui/window_services_confirm.py): _confirm_headline
gains a mouse_control branch, placed FIRST -- the write branch
below matches "move", so a mouse move would otherwise read
"This will change things on this PC." (verified by the repro's
fail-first run: move read exactly that, the clicks read the
generic). The branch dispatches on the action to a new pure
helper, _mouse_headline(summary):
  click        -> "This will click at 640, 400."
  double_click -> "This will double-click at 100, 200."
  right_click  -> "This will right-click at 100, 200."
  move         -> "This will move the mouse pointer to 640, 400."
  scroll       -> "This will scroll up at 640, 400." (amount + = up)
  no point     -> "... where the mouse pointer is." (clicks) /
                  "This will move the mouse pointer." (move)
  unknown action -> "This will use the mouse."
mouse_control's x/y are TRUE screen pixels, so naming them is
literally true. The other pointer tools are NOT covered here --
different headline shape, each needs its own measured fix:
click_anywhere / browser_click / click_element / uia_click_control
/ click_by_mark name a target (target=/name=/mark_id=), not
coordinates; cursor_control's default coords are norm1000, so a
coordinate headline there would misstate the point; drag_mouse
carries x1/y1/x2/y2. Each still falls back to its generic line
today. Sibling headlines (delete, run_command, the generic
fallback itself) are unchanged.

GUI tests are Qt-only (the real MainWindow offscreen), gated the
same way as the E-F202/E-F203/E-F205/E-F206/E-F207/E-F208/E-F209
suites (skipped on the sys interpreter, which has no PySide6).
Source pins run on both interpreters. The dialog is built
through the same builder slice 3 used:
ServicesConfirmMixin._build_confirm_dialog.
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

CONFIRM = ROOT / "gui" / "window_services_confirm.py"


class TestFixInSource(unittest.TestCase):
    """The fix is present in the product source — pins run on
    both interpreters."""

    def test_mouse_branch_exists_and_sits_first(self):
        src = CONFIRM.read_text(encoding="utf-8")
        self.assertIn("def _mouse_headline(summary: str) -> str:", src)
        self.assertIn('if "mouse_control" in low:', src)
        self.assertIn("return _mouse_headline(summary)", src)
        # E-F304: the branch must sit BEFORE the write branch --
        # the write branch matches "move", so a mouse move would
        # otherwise read "This will change things on this PC."
        self.assertLess(src.index('if "mouse_control" in low:'),
                        src.index('("write", "replace", "save",'))

    def test_mouse_headline_covers_all_five_actions(self):
        src = CONFIRM.read_text(encoding="utf-8")
        self.assertIn('if action == "click":', src)
        self.assertIn('if action == "double_click":', src)
        self.assertIn('if action == "right_click":', src)
        self.assertIn('if action == "move":', src)
        self.assertIn('if action == "scroll":', src)
        self.assertIn('"This will click at {point}."', src)
        self.assertIn('"This will double-click at {point}."', src)
        self.assertIn('"This will right-click at {point}."', src)
        self.assertIn(
            '"This will move the mouse pointer to {point}."', src)
        self.assertIn(
            '"This will click where the mouse pointer is."', src)

    def test_scroll_direction_comes_from_amount(self):
        src = CONFIRM.read_text(encoding="utf-8")
        self.assertIn('_confirm_summary_value(summary, ("amount",))',
                      src)
        self.assertIn('"This will scroll{direction} at {point}."', src)

    def test_branch_comment_names_the_finding(self):
        src = CONFIRM.read_text(encoding="utf-8")
        self.assertIn("E-F304", src)

    def test_sibling_headlines_unchanged(self):
        src = CONFIRM.read_text(encoding="utf-8")
        self.assertIn('"This will remove {target}."', src)
        self.assertIn('"This will run a command on this PC."', src)
        self.assertIn('VYUK wants to run \u201c{friendly}\u201d.', src)


def _pump(app, seconds=0.4):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def _preimport_w9():
    """Synthesize the voice_assistant package and pre-import the
    w9 chain BEFORE WindowBase installs its shim (E-F201 lesson:
    without this, lazy facade resolution fails and panels that
    build behind it never appear)."""
    import types as _types

    _pkg = _types.ModuleType("voice_assistant")
    _pkg.__path__ = [str(ROOT)]
    sys.modules["voice_assistant"] = _pkg
    _core_pkg = _types.ModuleType("voice_assistant.core")
    _core_pkg.__path__ = [str(ROOT / "core")]
    sys.modules["voice_assistant.core"] = _core_pkg
    _gui_pkg = _types.ModuleType("voice_assistant.gui")
    _gui_pkg.__path__ = [str(ROOT / "gui")]
    sys.modules["voice_assistant.gui"] = _gui_pkg
    from voice_assistant.gui import w9_wiring_proof  # noqa: F401


@unittest.skipUnless(_QT, "Qt-only: the real MainWindow offscreen")
class TestMouseHeadline(unittest.TestCase):
    """The dialog names the mouse action, in the same voice as
    the other headlines."""

    def setUp(self):
        _preimport_w9()
        from voice_assistant.gui.window_services_confirm import (  # noqa: E402
            ServicesConfirmMixin, _confirm_headline)
        self._confirm_headline = _confirm_headline
        self._build_dialog = ServicesConfirmMixin._build_confirm_dialog
        self._base = WindowBase()
        self._base.setUp()
        self.window = self._base.window
        self.app = self._base.app
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — overlay best-effort
            pass
        _pump(self.app)

    def tearDown(self):
        try:
            self._base.tearDown()
        except Exception:  # noqa: BLE001 — teardown best-effort
            pass

    def _dialog_headline(self, tool, summary):
        dlg = self._build_dialog(self.window, tool, summary)
        dlg.show()
        _pump(self.app, 0.3)
        text = self.window._confirm_headline_label.text()
        dlg.close()
        _pump(self.app, 0.2)
        return text

    def test_click_headline_names_the_point(self):
        self.assertEqual(
            self._confirm_headline(
                "mouse_control", "action=click, x=640, y=400"),
            "This will click at 640, 400.")

    def test_all_five_actions_and_no_point(self):
        headline = self._confirm_headline
        self.assertEqual(
            headline("mouse_control", "action=double_click, x=100, y=200"),
            "This will double-click at 100, 200.")
        self.assertEqual(
            headline("mouse_control", "action=right_click, x=100, y=200"),
            "This will right-click at 100, 200.")
        self.assertEqual(
            headline("mouse_control", "action=move, x=640, y=400"),
            "This will move the mouse pointer to 640, 400.")
        self.assertEqual(
            headline("mouse_control",
                     "action=scroll, x=640, y=400, amount=-3"),
            "This will scroll down at 640, 400.")
        self.assertEqual(
            headline("mouse_control", "action=click"),
            "This will click where the mouse pointer is.")

    def test_real_dialog_headline_for_the_filed_shape(self):
        self.assertEqual(
            self._dialog_headline(
                "mouse_control", "action=click, x=640, y=400"),
            "This will click at 640, 400.")

    def test_sibling_dialog_headlines_unchanged(self):
        self.assertEqual(
            self._dialog_headline(
                "delete_file",
                "action=delete, path=C:/Users/Aayur/Documents/report.docx"),
            "This will remove the file report.docx.")
        self.assertEqual(
            self._dialog_headline("run_command", "command=dir"),
            "This will run a command on this PC.")

    def test_generic_fallback_still_fires_for_other_tools(self):
        self.assertEqual(
            self._dialog_headline("get_system_info", ""),
            "VYUK wants to run \u201cSystem status\u201d.")
