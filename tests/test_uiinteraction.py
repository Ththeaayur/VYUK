"""UI interaction slice: palette failure surface, dialog focus policy,
notification state matrix, palette empty/large sets (v5.97 UI audit).

Static pins run on every interpreter. Widget tests need PySide6 and
skip cleanly without it (ImportError -> skipTest, the doctorui /
audit2s1 pattern). The widget tests import the REAL gui modules
through stub voice_assistant.core.voice.audio/.brain modules (used only
inside methods the tests never call, so attribute stubs suffice);
the stubs are removed in tearDownModule so later suites in the same
process are not poisoned.

NOTIFICATION STATE MATRIX (pinned here, not invented):
- Inline notices (ChatMixin._notify) are persistent chat lines:
  they never auto-dismiss, they never rewrite the caller's text,
  and they claim nothing beyond the text itself.
- Tray messages (window_services confirm fallback) auto-dismiss
  after 5000 ms, fire ONLY when the window is not active and a tray
  icon exists, and only for confirmation requests. Repeated tray
  messages replace (Qt behaviour), they do not stack.
- Confirm dialogs are modal until answered; Enter denies by
  default (the safe button), never allows.
- No notification mechanism upgrades a dispatch into an outcome
  claim: the tray text says "waiting for your answer", the palette
  failure dialog says the action "didn't run".

DIALOG FOCUS POLICY (pinned here):
- Initial focus lands on the sensible default, never a destructive
  action: palette -> search input; confirm dialogs -> "Don't
  allow"; cloud-switch offer -> "Keep on this PC"; read-only list
  dialogs -> Close is the default button.
- Every HudDialog.confirm in gui/ defaults to No (the method's
  own default); no call passes default=True.
- Focus returns to the invoking control when the palette closes.
- Escape closes/cancels: palette (explicit handler) and QDialogs
  (Qt's default reject path, pinned for the confirm dialog).
- First-open (full-sentence choices, No default) and onboarding
  Space/Return handling are UNTOUCHED by this slice; their suites
  stay green as neighbours.
"""

import ast
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
GUI = ROOT / "gui"

try:
    from PySide6.QtWidgets import QApplication  # noqa: F401
    _QT = True
except ImportError:
    _QT = False

_APP = None

# Audit fix Batch 5 (TEST-03): snapshot slot for the
# voice_assistant.* sys.modules entries — same discipline as
# test_uia11y.py. The old unconditional delete-all was a
# second live instance of the audit2s1 pattern: in a Qt-less
# env setUpModule early-returned without installing anything,
# but tearDownModule still deleted every voice_assistant.*
# entry, including chatfix's import-time-cached real
# w9_wiring when uiinteraction ran first.
_VA_SNAPSHOT = None


def setUpModule():
    global _APP, _VA_SNAPSHOT
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    if not _QT:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication as _QA
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    tree = str(ROOT)
    pkg = types.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = types.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    audio = types.ModuleType("voice_assistant.core.voice.audio")  # Phase 3 V1a-1 INTENTIONAL: stub follows audio move
    audio.audio_manager = types.SimpleNamespace()
    brain = types.ModuleType("voice_assistant.core.brain.brain")  # Phase 3 B2h: brain hub moved to core/brain/brain.py INTENTIONAL: stub follows hub move
    brain.live_session = types.SimpleNamespace()
    for name, mod in (("voice_assistant", pkg),
                      ("voice_assistant.core", core),
                      ("voice_assistant.core.voice.audio", audio),  # Phase 3 V1a-1 INTENTIONAL: stub follows audio move
                      ("voice_assistant.core.brain.brain", brain)):  # Phase 3 B2h: brain hub moved to core/brain/brain.py INTENTIONAL: stub follows hub move
        sys.modules[name] = mod


def tearDownModule():
    global _VA_SNAPSHOT
    if _VA_SNAPSHOT is None:
        return
    for key in list(sys.modules):
        if key == "voice_assistant" or key.startswith("voice_assistant."):
            del sys.modules[key]
    sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None


def _read(name):
    return (GUI / name).read_text(encoding="utf-8")


def _confirm_defaults():
    """Every HudDialog.confirm call in gui/: (file, default-src).

    ``default`` is None when the call relies on confirm's own
    default (False — No); a 4th positional arg or a default=
    keyword overrides it. Destructive calls pass destructive=
    instead, which this scanner ignores on purpose.
    """
    found = []
    for path in sorted(GUI.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and func.attr == "confirm"):
                continue
            value = func.value
            if not (isinstance(value, ast.Name)
                    and value.id == "HudDialog"):
                continue
            default = None
            for kw in node.keywords:
                if kw.arg == "default":
                    default = ast.get_source_segment(src, kw.value)
            if default is None and len(node.args) >= 4:
                default = ast.get_source_segment(src, node.args[3])
            found.append((path.name, default))
    return found


# ------------------------------------------------------------------ static
class TestInteractionStaticPins(unittest.TestCase):
    def test_confirm_dialogs_all_default_no(self):
        # Phase C: questions are HudDialog.confirm, whose own
        # default is No — and no call may pass default=True. All
        # 8 calls at Phase C time rely on that default (the
        # window_pages reset-settings call previously hid behind
        # a local _QMB alias).
        found = _confirm_defaults()
        self.assertGreaterEqual(len(found), 7,
                                "expected at least the 7 known call sites")
        for fname, default in found:
            self.assertTrue(
                default is None or default.strip() == "False",
                f"{fname}: confirm default {default!r} is not No")

    def test_confirm_dialogs_default_deny(self):
        # Both confirm dialogs (designed + plain fallback) pin the
        # safe button as default.
        # GUI split #10 batch 1 INTENTIONAL: both dialogs moved
        # verbatim to window_services_confirm.py. Assertion unchanged.
        src = _read("window_services_confirm.py")
        self.assertEqual(src.count("btn_deny.setDefault(True)"), 2)
        self.assertEqual(src.count("btn_deny.setFocus()"), 2)

    def test_cloud_switch_default_keep(self):
        # GUI split #4 INTENTIONAL: _offer_cloud_switch moved verbatim
        # to window_brain_cloud.py. Assertions unchanged.
        self.assertIn("btn_keep.setDefault(True)", _read("window_brain_cloud.py"))
        self.assertIn("btn_keep.setFocus()", _read("window_brain_cloud.py"))

    def test_readonly_dialogs_default_close(self):
        self.assertIn("btn_close.setDefault(True)",
                      _read("window_activity.py"))
        self.assertIn("btn_close.setDefault(True)",
                      # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
                      _read("window_shell_activity.py"))

    def test_palette_failure_surface_present(self):
        # INTENTIONAL (refactor P1 step 5): CommandPalette moved verbatim
        # to window_widgets_palette.py. Assertions unchanged.
        src = _read("window_widgets_palette.py")
        seg_start = src.index("def _run_row")
        seg = src[seg_start:seg_start + 3000]
        self.assertIn("HudDialog.critical", seg)
        self.assertIn("_last_action_error", seg)

    def test_palette_focus_return_present(self):
        # INTENTIONAL (refactor P1 step 5): CommandPalette moved verbatim
        # to window_widgets_palette.py. Assertions unchanged.
        src = _read("window_widgets_palette.py")
        self.assertIn("_focus_return", src)
        self.assertIn("def closeEvent", src)

    def test_tray_notification_matrix_source(self):
        # Tray fallback: inactive-window guard + 5 s auto-dismiss,
        # confirm-requests only (the surrounding method is the
        # confirm-requested slot).
        # GUI split #10 batch 1 INTENTIONAL: _on_confirm_requested
        # moved verbatim to window_services_confirm.py.
        src = _read("window_services_confirm.py")
        idx = src.index("tray.showMessage")
        window = src[max(0, idx - 500):idx + 400]
        self.assertIn("isActiveWindow", window)
        self.assertIn("5000", window)
        self.assertIn("waiting for your answer", window)

    def test_notify_passes_text_verbatim(self):
        # The inline-notice mechanism must not rewrite or upgrade
        # the caller's wording: _notify hands its text straight to
        # the persistent system line.
        # GUI split #9 INTENTIONAL: _notify lives in the bubbles module now.
        src = _read("window_chat_bubbles.py")
        seg_start = src.index("def _notify")
        seg = src[seg_start:seg_start + 300]
        self.assertIn("_add_system(text)", seg)


# --------------------------------------------------------------------- Qt
class TestInteractionQt(unittest.TestCase):
    def setUp(self):
        if not _QT:
            self.skipTest("PySide6 unavailable in this interpreter")

    def _pump(self):
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def _palette(self, parent):
        from voice_assistant.gui.window_widgets import CommandPalette
        pal = CommandPalette(parent)
        return pal

    def _fake_entries(self, n, fail_at=None):
        entries = []
        for i in range(n):
            def _ok():
                return None

            def _boom():
                raise RuntimeError("kaboom")

            entries.append({
                "label": f"Command {i:02d}",
                "action": _boom if i == fail_at else _ok,
                "kind": "PAGE", "description": "",
                "action_id": f"cmd_{i}", "consequential": False})
        return entries

    def test_palette_failure_surfaces_and_success_clears(self):
        from PySide6.QtWidgets import QWidget
        from voice_assistant.gui.design_system.dialogs import HudDialog
        parent = QWidget()
        parent.show()
        self._pump()
        pal = self._palette(parent)
        pal._commands = self._fake_entries(2, fail_at=0)
        pal._apply_filter("")
        self.assertEqual(len(pal._visible_actions), 2)
        with mock.patch.object(HudDialog, "critical") as crit:
            pal._run_row(0)
            self.assertEqual(crit.call_count, 1)
            shown = crit.call_args[0][2]
            self.assertIn("Command 00", shown)
            self.assertIn("kaboom", shown)
            self.assertIn("Command 00", pal._last_action_error)
            self.assertIn("kaboom", pal._last_action_error)
            # A later success clears the record: the palette never
            # reports a failed action as done, nor a done action
            # as failed.
            pal._run_row(1)
            self.assertEqual(crit.call_count, 1)
            self.assertIsNone(pal._last_action_error)
        pal.close()
        parent.close()

    def test_palette_consequential_decline_runs_nothing(self):
        from PySide6.QtWidgets import QWidget
        from voice_assistant.gui.design_system.dialogs import HudDialog
        parent = QWidget()
        parent.show()
        self._pump()
        pal = self._palette(parent)
        ran = []
        pal._commands = [{
            "label": "Connect / Disconnect",
            "action": lambda: ran.append(True),
            "kind": "SETTING", "description": "",
            "action_id": "toggle_connection", "consequential": True}]
        pal._apply_filter("")
        with mock.patch.object(
                HudDialog, "confirm", return_value=False):
            pal._run_row(0)
        self.assertEqual(ran, [])
        self.assertIsNone(pal._last_action_error)
        pal.close()
        parent.close()

    def test_palette_empty_state(self):
        from PySide6.QtWidgets import QWidget
        parent = QWidget()
        parent.show()
        self._pump()
        pal = self._palette(parent)
        pal._commands = self._fake_entries(2)
        pal._apply_filter("zzzz-no-match")
        self.assertEqual(pal._visible_actions, [])
        self.assertEqual(pal.list.count(), 1)
        self.assertEqual(pal.list.currentRow(), -1)
        widget = pal.list.itemWidget(pal.list.item(0))
        self.assertIn("No matching commands", widget.text())
        # Enter/click on the empty state runs nothing, crashes
        # nothing, and records no error.
        pal._activate_current()
        self.assertIsNone(pal._last_action_error)
        # Clearing the filter restores real rows with no stale
        # empty-state row left behind.
        pal._apply_filter("")
        self.assertEqual(len(pal._visible_actions), 2)
        self.assertEqual(pal.list.count(), 2)
        self.assertEqual(pal.list.currentRow(), 0)
        pal.close()
        parent.close()

    def test_palette_large_set_caps_and_nav_stays_actionable(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QWidget
        parent = QWidget()
        parent.show()
        self._pump()
        pal = self._palette(parent)
        pal._commands = self._fake_entries(30)
        pal._apply_filter("")
        self.assertEqual(len(pal._visible_actions), 12)
        self.assertEqual(pal.list.count(), 13)
        cap = pal.list.itemWidget(pal.list.item(12))
        self.assertIn("18 more", cap.text())
        pal.open_centered()
        self._pump()
        self.assertEqual(pal.list.currentRow(), 0)
        for _ in range(25):
            QTest.keyClick(pal.input, Qt.Key.Key_Down)
        self._pump()
        # Navigation never lands on the display-only cap line.
        self.assertEqual(pal.list.currentRow(), 11)
        for _ in range(25):
            QTest.keyClick(pal.input, Qt.Key.Key_Up)
        self._pump()
        self.assertEqual(pal.list.currentRow(), 0)
        pal.close()
        parent.close()

    def test_palette_focus_input_esc_close_focus_returns(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QPushButton, QWidget
        parent = QWidget()
        btn = QPushButton("Invoke", parent)
        btn.show()
        parent.show()
        self._pump()
        btn.setFocus()
        self._pump()
        self.assertIs(QApplication.focusWidget(), btn)
        pal = self._palette(parent)
        pal.open_centered()
        self._pump()
        self.assertIs(QApplication.focusWidget(), pal.input)
        QTest.keyClick(pal.input, Qt.Key.Key_Escape)
        self._pump()
        self.assertFalse(pal.isVisible())
        # Offscreen artifact, documented not hidden: closing a
        # modal leaves NO window active offscreen (real Windows
        # reactivates the parent itself), so focus cannot be
        # observed until the parent is reactivated. The deferred
        # closeEvent restore recorded the invoker as the parent's
        # focus widget; reactivation must land focus on it.
        parent.activateWindow()
        self._pump()
        self.assertIs(QApplication.focusWidget(), btn)
        parent.close()

    def test_confirm_dialogs_default_deny_and_escape(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QDialog, QWidget
        from voice_assistant.gui.window_services import ServicesMixin
        for builder in (ServicesMixin._build_confirm_dialog,
                        ServicesMixin._build_plain_confirm_dialog):
            fake = QWidget()
            fake.show()
            self._pump()
            dlg = builder(fake, "some_tool", "do a thing")
            dlg.show()
            self._pump()
            # QDialog has no defaultButton(); the default is the
            # button with isDefault().
            from PySide6.QtWidgets import QPushButton
            defaults = [b for b in dlg.findChildren(QPushButton)
                        if b.isDefault()]
            self.assertEqual(len(defaults), 1)
            self.assertEqual(defaults[0].text(), "Don't allow")
            self.assertEqual(dlg.focusWidget().text(), "Don't allow")
            QTest.keyClick(dlg, Qt.Key.Key_Escape)
            self._pump()
            self.assertEqual(dlg.result(), QDialog.DialogCode.Rejected)
            dlg.close()
            fake.close()

    def test_cloud_switch_default_keep(self):
        from PySide6.QtWidgets import QDialog, QWidget
        from voice_assistant.gui.window_brain import BrainMixin
        captured = []

        def _fake_exec(self):
            captured.append(self)
            return QDialog.DialogCode.Rejected

        fake = QWidget()
        fake.show()
        self._pump()
        with mock.patch.object(QDialog, "exec", _fake_exec):
            result = BrainMixin._offer_cloud_switch(fake)
        self.assertFalse(result)
        self.assertEqual(len(captured), 1)
        dlg = captured[0]
        from PySide6.QtWidgets import QPushButton
        defaults = [b for b in dlg.findChildren(QPushButton)
                    if b.isDefault()]
        self.assertEqual(len(defaults), 1)
        self.assertEqual(defaults[0].text(), "Keep on this PC")
        dlg.close()
        fake.close()

    def test_notify_persists_verbatim(self):
        from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
        from voice_assistant.gui.window_chat import ChatMixin
        host = QWidget()
        layout = QVBoxLayout(host)
        shown = []

        class _Stub:
            pass

        stub = _Stub()
        stub.chat_layout = layout
        stub._group_break = False
        stub._scroll_chat_to_bottom = lambda: None
        stub._show_page = lambda idx: shown.append(idx)
        # _notify delegates to the real _add_system; bind it onto
        # the stub so the test drives real code, not a fake.
        stub._add_system = lambda text: ChatMixin._add_system(stub, text)
        host.show()
        self._pump()
        ChatMixin._notify(stub, "First notice")
        ChatMixin._notify(stub, "Second notice")
        self._pump()
        self.assertEqual(shown, [0, 0])
        self.assertEqual(layout.count(), 2)
        # Build 1: a system line rides in a row layout (the row's
        # top margin is the gap), so each top-level item is a
        # layout, not the label — unwrap the row to read the
        # label. (Pre-existing failure, failing since Build 1's
        # row change on the pre-Build-4 tree too; surfaced by
        # Build 4's neighbour run, which was the first neighbour
        # set since Build 1 to include this suite.)
        def _row_widget(i):
            row = layout.itemAt(i).layout()
            self.assertIsNotNone(row)
            widget = row.itemAt(0).widget()
            self.assertIsNotNone(widget)
            return widget

        texts = [_row_widget(i).text()
                 for i in range(layout.count())]
        self.assertEqual(texts, ["First notice", "Second notice"])
        kinds = [_row_widget(i).objectName()
                 for i in range(layout.count())]
        self.assertEqual(kinds, ["systemLine", "systemLine"])
        host.close()


if __name__ == "__main__":
    unittest.main()
