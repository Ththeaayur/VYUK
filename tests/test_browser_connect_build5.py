"""Rethink Build 5 — Chrome connect end-to-end (§9): the three gaps
the lab audit found, and the proofs they stay fixed.

The audit (rethink_build5_work/audit_notes.md) ran the §9 flow
against real Chromium: every enable branch attaches or refuses
honestly, the real tap connects with tab count + tab name, and
the skip path (refusal -> strip -> dialog -> Connect) works.
Three gaps were in the GUI/copy layer, not the state machine:

* F1 — the honest-cost line (ASK_COPY["browser"]["cost_line"])
  was dead copy: the widget never rendered it, and the computed
  path line names no cost on the "unknown" path. §9.1 step 0
  requires the cost before the tap.
* F3 — the wizard Browser page had no "Not now" link (§9.1 step
  0). It records nothing durable — the browser stays unknown,
  exactly as the wizard's global Continue leaves it.
* F2 — the first-launch Home-strip offer (§9.1) did not exist:
  one quiet offer, dismissible, never repeated.

Harness mirrors test_first_run_onboarding.py: the GUI reads
permission_grants / settings_voice / browser_enable through the
``voice_assistant.core`` alias (the dual-import trap), the grants
store is pointed at a temp dir per test, and the QSettings offer
key is removed in setUp/tearDown so no test pollutes the real
store (the fix #15 lesson: clear keys explicitly).
"""

import json
import os
import pathlib
import sys
import tempfile
import types as _types_mod
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001 — lab has no PortAudio
    _fake_sd = _types_mod.ModuleType("sounddevice")
    sys.modules["sounddevice"] = _fake_sd

try:
    from PySide6.QtWidgets import QApplication as _QA
    from PySide6.QtWidgets import QLabel as _QLabel
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _QA = None
    _QLabel = None
    _QT = False

from core import first_run_asks as _asks  # noqa: E402

_APP = None
_VA_SNAPSHOT = None
OnboardingDialog = None
BrowserConnectWidget = None
BrowserConnectDialog = None
open_browser_connect_dialog = None
ShellAssistantMixin = None
settings_voice = None
permission_grants = None
browser_enable = None

_OFFER_KEY = "browser/connect_offer_shown"


def setUpModule():
    """Mirror test_first_run_onboarding's voice_assistant.* alias."""
    global _APP, _VA_SNAPSHOT, _QT
    global OnboardingDialog, BrowserConnectWidget
    global BrowserConnectDialog, open_browser_connect_dialog
    global ShellAssistantMixin, settings_voice
    global permission_grants, browser_enable
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    if not _QT:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    tree = str(ROOT)
    pkg = _types_mod.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = _types_mod.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    sys.modules["voice_assistant"] = pkg
    sys.modules["voice_assistant.core"] = core
    try:
        from gui.onboarding import (  # noqa: E402
            OnboardingDialog as _OD)
        from gui.browser_connect_widget import (  # noqa: E402
            BrowserConnectWidget as _BCW)
        from gui.browser_connect_dialog import (  # noqa: E402
            BrowserConnectDialog as _BCD,
            open_browser_connect_dialog as _open_bcd)
        from gui.window_shell_assistant import (  # noqa: E402
            ShellAssistantMixin as _SAM)
        from voice_assistant.core.gui_facade import (  # noqa: E402
            settings_voice as _sv,
            permission_grants as _pg,
            browser_enable as _be)
        OnboardingDialog = _OD
        BrowserConnectWidget = _BCW
        BrowserConnectDialog = _BCD
        open_browser_connect_dialog = _open_bcd
        ShellAssistantMixin = _SAM
        settings_voice = _sv
        permission_grants = _pg
        browser_enable = _be
    except Exception:  # noqa: BLE001 — GUI import failed, skip
        _QT = False


def tearDownModule():
    global _VA_SNAPSHOT
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        del sys.modules[key]
    if _VA_SNAPSHOT is not None:
        sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None


def _remove_offer_key():
    """Drop the durable offer flag from the real QSettings store
    (tests only — the fix #15 lesson: clear keys explicitly)."""
    try:
        from core.config_spec import qsettings_remove as _remove

        _remove(_OFFER_KEY)
    except Exception:  # noqa: BLE001 — no Qt, no key, no problem
        pass


class _DataGuard:
    """Restore data/ after a test (consumers-batch pattern)."""

    def _scan(self):
        found = {}
        for path in sorted((ROOT / "data").rglob("*")):
            if path.is_file():
                try:
                    found[path.relative_to(ROOT / "data")] = \
                        path.read_bytes()
                except OSError:
                    pass
        return found

    def __enter__(self):
        self._saved = self._scan()
        return self

    def __exit__(self, *exc):
        current = self._scan()
        for rel in current:
            if rel not in self._saved:
                try:
                    ((ROOT / "data") / rel).unlink()
                except OSError:
                    pass
        for rel, data in self._saved.items():
            path = (ROOT / "data") / rel
            try:
                if not path.exists() or path.read_bytes() != data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            except OSError:
                pass
        return False


class _StoreMixin(unittest.TestCase):
    """Temp-dir grants store + data/ guard + settings_voice reset
    (mirrors test_first_run_onboarding._StoreMixin)."""

    def setUp(self):
        super().setUp()
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
        self._guard = _DataGuard()
        self._guard.__enter__()
        if settings_voice is not None:
            settings_voice.reset_for_tests()
        _remove_offer_key()

    def tearDown(self):
        if settings_voice is not None:
            settings_voice.reset_for_tests()
        _remove_offer_key()
        self._guard.__exit__()
        self._pg.clear_path_override()
        self._pg.clear_session_grants()
        self._tmp.cleanup()
        super().tearDown()


@unittest.skipUnless(_QT, "Qt required")
class CostLineTests(_StoreMixin):
    """F1 — the honest-cost line shows exactly where the path
    line does not already state the cost (§9.1 step 0)."""

    @classmethod
    def setUpClass(cls):
        cls.app = _QA.instance() or _QA([])

    def _widget(self):
        widget = BrowserConnectWidget()
        self.addCleanup(widget.deleteLater)
        return widget

    def _refresh_with(self, widget, path, line):
        with mock.patch.object(
                browser_enable, "browser_connect_path",
                return_value={"path": path, "line": line}):
            widget._refresh_path_line()

    def test_unknown_path_shows_the_cost_line(self):
        widget = self._widget()
        self._refresh_with(
            widget, "unknown",
            "VYUK will check how to reach your Chrome when you "
            "tap Connect my browser.")
        self.assertEqual(
            widget.path_label.text(),
            "VYUK will check how to reach your Chrome when you "
            "tap Connect my browser.")
        self.assertEqual(
            widget.cost_label.text(),
            _asks.ASK_COPY["browser"]["cost_line"])
        self.assertIn("separate VYUK profile",
                      widget.cost_label.text())

    def test_launch_path_does_not_repeat_the_cost(self):
        # The launch line already states the separate-profile
        # cost — showing the cost line too would state it twice
        # (pins test_browser_page_renders_the_widget's
        # "If Chrome has to restart" absence on this path).
        widget = self._widget()
        self._refresh_with(
            widget, "launch",
            "On this PC, Chrome isn't running, so connecting will "
            "open it on a separate VYUK profile.")
        self.assertEqual(widget.cost_label.text(), "")
        self.assertIn("separate VYUK profile",
                      widget.path_label.text())

    def test_direct_path_pays_no_cost(self):
        widget = self._widget()
        self._refresh_with(
            widget, "direct",
            "On this PC, VYUK will drive your Chrome as it is.")
        self.assertEqual(widget.cost_label.text(), "")

    def test_cost_line_survives_a_probe_failure(self):
        # A failed probe keeps the previous lines — never blanks
        # them, never raises (the #45 contract, unchanged).
        widget = self._widget()
        self._refresh_with(widget, "unknown", "Some line.")
        before = widget.cost_label.text()
        with mock.patch.object(
                browser_enable, "browser_connect_path",
                side_effect=RuntimeError("probe died")):
            widget._refresh_path_line()
        self.assertEqual(widget.cost_label.text(), before)


@unittest.skipUnless(_QT, "Qt required")
class NotNowTests(_StoreMixin):
    """F3 — the quiet "Not now" link (§9.1 step 0)."""

    @classmethod
    def setUpClass(cls):
        cls.app = _QA.instance() or _QA([])

    def _widget(self):
        widget = BrowserConnectWidget()
        self.addCleanup(widget.deleteLater)
        return widget

    def test_not_now_link_exists_and_skips_quietly(self):
        widget = self._widget()
        self.assertEqual(widget.btn_not_now.text(), "Not now")
        seen = []
        widget.not_now_requested.connect(lambda: seen.append(True))
        widget.btn_not_now.click()
        self.assertTrue(widget.skipped)
        self.assertTrue(seen)
        self.assertTrue(widget.btn_connect.isHidden())
        self.assertTrue(widget.btn_not_now.isHidden())
        self.assertIn("Not now — skipped.",
                      widget.status_label.text())
        self.assertIn("ask again when you use the browser",
                      widget.status_label.text())
        # Records nothing durable — no grants store file, no
        # browser grant, no opt-in flip.
        self.assertFalse(self._store.exists())
        self.assertNotEqual(
            permission_grants.get_state("browser"), "granted")

    def test_not_now_while_busy_does_nothing(self):
        widget = self._widget()
        widget._set_busy(True, "Connecting to your Chrome…")
        widget.btn_not_now.click()
        self.assertFalse(widget.skipped)
        self.assertFalse(widget.btn_connect.isHidden())

    def test_wizard_page_records_the_skip(self):
        dlg = OnboardingDialog()
        self.addCleanup(dlg.deleteLater)
        page = dlg._browser_page
        widget = page.widget
        self.assertEqual(page.ask_result, "unanswered")
        widget.btn_not_now.click()
        self.assertEqual(page.ask_result, "skipped")
        self.assertFalse(self._store.exists())
        self.assertNotEqual(
            permission_grants.get_state("browser"), "granted")

    def test_dialog_not_now_closes_it(self):
        from PySide6.QtWidgets import QWidget

        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        open_browser_connect_dialog(owner)
        dlg = owner._browser_connect_dialog
        self.assertIsInstance(dlg, BrowserConnectDialog)
        dlg.widget.btn_not_now.click()
        _QA.processEvents()
        self.assertIsNone(owner._browser_connect_dialog)

    def test_check_again_reruns_enable(self):
        # The toggle path's "Check again" re-runs the enable flow
        # (the probe + attach) — the permission and opt-in are
        # already on, so it must NOT re-flip them.
        widget = self._widget()
        widget._on_connect_result(json.dumps(
            {"state": "needs_restart",
             "message": "Chrome is running without the port."}))
        self.assertFalse(widget.toggle_block.isHidden())
        calls = []
        with mock.patch.object(
                browser_enable, "enable_browser_control",
                side_effect=lambda: calls.append(True) or {
                    "state": "attached",
                    "message": "Attached to your Chrome. "
                               "1 tab(s) open. Active tab: 'x'."}):
            widget.btn_check_again.click()
            deadline = __import__("time").monotonic() + 10
            while (("Connected" not in widget.status_label.text())
                   and __import__("time").monotonic() < deadline):
                _QA.processEvents()
                __import__("time").sleep(0.05)
            _QA.processEvents()
        self.assertTrue(calls)
        self.assertIn("Connected — VYUK can see your Chrome.",
                      widget.status_label.text())


class OfferFlagTests(unittest.TestCase):
    """F2 — the offer flags (settings_voice). The durable flag
    lives in QSettings, so its tests need Qt; the session flag
    is plain module state."""

    def setUp(self):
        super().setUp()
        from core import settings_voice as _flat_sv

        self._sv = _flat_sv
        self._sv.reset_for_tests()
        _remove_offer_key()

    def tearDown(self):
        self._sv.reset_for_tests()
        _remove_offer_key()
        super().tearDown()

    def test_session_flag_lifecycle(self):
        self.assertFalse(self._sv.browser_offer_active())
        self._sv.note_browser_offer_shown()
        self.assertTrue(self._sv.browser_offer_active())
        self._sv.clear_browser_offer()
        self.assertFalse(self._sv.browser_offer_active())

    def test_reset_clears_the_session_flag(self):
        self._sv.note_browser_offer_shown()
        self._sv.reset_for_tests()
        self.assertFalse(self._sv.browser_offer_active())

    @unittest.skipUnless(_QT, "Qt required for QSettings writes")
    def test_durable_flag_is_set_on_show(self):
        self.assertFalse(self._sv.browser_offer_shown())
        self._sv.note_browser_offer_shown()
        self.assertTrue(self._sv.browser_offer_shown())
        # Clearing the session flag does NOT clear the durable
        # one — the offer never repeats on a later launch.
        self._sv.clear_browser_offer()
        self.assertTrue(self._sv.browser_offer_shown())


@unittest.skipUnless(_QT, "Qt required")
class OfferStripTests(_StoreMixin):
    """F2 — the first-launch offer on the Home strip (§9.1):
    one quiet offer, dismissible, never repeated."""

    @classmethod
    def setUpClass(cls):
        cls.app = _QA.instance() or _QA([])

    def _window(self):
        import types as _t
        from PySide6.QtWidgets import (
            QFrame, QHBoxLayout, QLabel, QPushButton)

        ns = _t.SimpleNamespace()
        ns.settings_voice_strip = QFrame()
        lay = QHBoxLayout(ns.settings_voice_strip)
        ns.settings_voice_strip_label = QLabel("")
        lay.addWidget(ns.settings_voice_strip_label)
        for name in ("yes", "no", "undo", "keep", "off", "fix",
                     "browser", "dismiss"):
            btn = QPushButton(name)
            setattr(ns, f"btn_settings_voice_{name}", btn)
            lay.addWidget(btn)
        ns._brain_fallback_active = False
        ns._connect_calls = []
        ns._card_calls = []
        ns._open_connect_dialog = lambda: ns._connect_calls.append(True)
        ns.show_settings_card = lambda t: ns._card_calls.append(t)
        ns._speak_strip_announcement = lambda text: None
        ns._show_strip_buttons = (
            lambda names: ShellAssistantMixin._show_strip_buttons(
                ns, names))
        ns._SESSION_GRANT_WORDS = (
            ShellAssistantMixin._SESSION_GRANT_WORDS)
        ns._refresh_settings_voice_strip = (
            lambda: ShellAssistantMixin._refresh_settings_voice_strip(
                ns))
        ns._on_settings_voice_dismiss = (
            lambda: ShellAssistantMixin._on_settings_voice_dismiss(ns))
        ns.btn_settings_voice_dismiss.clicked.connect(
            ns._on_settings_voice_dismiss)
        return ns

    def _refresh(self, ns):
        ShellAssistantMixin._refresh_settings_voice_strip(ns)

    def _visible_buttons(self, ns):
        return {name for name in
                ("yes", "no", "undo", "keep", "off", "fix", "browser",
                 "dismiss")
                if getattr(ns, f"btn_settings_voice_{name}").isVisible()}

    def test_offer_shows_once_with_dismiss(self):
        ns = self._window()
        with mock.patch.object(browser_enable, "_is_connected",
                               return_value=False):
            self._refresh(ns)
        self.assertTrue(ns.settings_voice_strip.isVisible())
        self.assertEqual(self._visible_buttons(ns),
                         {"browser", "dismiss"})
        self.assertEqual(ns.settings_voice_strip_label.text(),
                         "Connect my browser — one tap, about a "
                         "minute.")
        self.assertTrue(settings_voice.browser_offer_shown())
        # Same launch, still showing (the session flag keeps it
        # visible until dismissed or connected).
        with mock.patch.object(browser_enable, "_is_connected",
                               return_value=False):
            self._refresh(ns)
        self.assertTrue(ns.settings_voice_strip.isVisible())
        # Dismiss: hidden now, and never again.
        ns.btn_settings_voice_dismiss.click()
        self.assertFalse(ns.settings_voice_strip.isVisible())
        with mock.patch.object(browser_enable, "_is_connected",
                               return_value=False):
            self._refresh(ns)
        self.assertFalse(ns.settings_voice_strip.isVisible())

    def test_offer_never_shows_when_connected(self):
        ns = self._window()
        with mock.patch.object(browser_enable, "_is_connected",
                               return_value=True):
            self._refresh(ns)
        self.assertFalse(ns.settings_voice_strip.isVisible())
        self.assertFalse(settings_voice.browser_offer_shown())

    def test_refusal_outranks_the_offer(self):
        ns = self._window()
        settings_voice.note_browser_no_session()
        with mock.patch.object(browser_enable, "_is_connected",
                               return_value=False):
            self._refresh(ns)
        self.assertEqual(self._visible_buttons(ns), {"browser"})
        self.assertIn("isn't connected yet",
                      ns.settings_voice_strip_label.text())
        # The offer was not spent by the refusal — it can still
        # show on a later launch.
        self.assertFalse(settings_voice.browser_offer_shown())

    def test_offer_connect_clears_the_session_flag(self):
        # Connecting (the widget's success path) clears the
        # session flag; the durable flag stays set.
        settings_voice.note_browser_offer_shown()
        self.assertTrue(settings_voice.browser_offer_active())
        settings_voice.clear_browser_offer()
        self.assertFalse(settings_voice.browser_offer_active())


if __name__ == "__main__":
    unittest.main()
