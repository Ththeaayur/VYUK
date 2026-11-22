"""First-run onboarding: the wizard asks, one tap connects.

The fix for the owner's complaint that browser control told him
to "connect through settings" and that flow was far too
complicated. His direction: ask for everything at first open,
instead of a settings wall mid-task. The scope is
~/workspace/code-lab/first_run_onboarding_scope.md (§2 the ask
sequence, §3 the one-tap browser connect, §4 the hard rules,
§5 the build shape).

What this suite proves, per scope §5:

* Copy — every ask's user-facing text passes the first-open
  jargon scan (core/firstopen_optin.JARGON_TERMS); no banned
  term in any ask, button, or honest-cost line. The mic copy is
  the first-use dialog's copy, verbatim except the explainer
  (which names Connect — wrong in the wizard).
* Skip is safe — skipping any ask leaves that permission
  undecided (unknown / never-decided), never granted, except
  the mic page's "Not now", which records a durable off
  (field-check #9); the wizard still reaches Done; "Skip
  setup" exits and keeps the answers already given, while the
  brain, language, voice and profile picks are only saved at
  Finish (field-check #10).
* Grant path — tapping Yes on the mic ask writes via the
  Settings-only path (via="settings") and never via voice;
  tapping Yes on the browser page runs the enable state
  machine through the seams, no real Chrome; no ask writes
  anything on page entry — a page merely shown grants nothing.
* Never gates — Continue is never blocked by a permission ask;
  only the brain choice gates it.
* D8/D4/D5 pins — a stored denial defeats a session grant;
  a granted permission never skips a confirmation (D4, as
  changed by the UI rethink §4.2: except for the kinds the
  owner has explicitly always-allowed — the confirm-gated
  shape is pinned by the pf18 suites; here the session-grant
  leg is pinned); the browser page is CDP-only (the add-on
  line is on the page, the extension manifest is untouched
  — ResoBin-only host permissions).
* Render/walkthrough (Qt) — the wizard renders offscreen with
  the new pages in §2 order; each ask page shows its Yes/Skip
  buttons; the browser page shows the connect button and the
  honest-cost line; the strip shows "Connect my browser" after
  a simulated no-session refusal; the standalone connect
  dialog opens from the strip's opener with a double-open
  guard.
* Fallback — after skipping the browser in the wizard, a
  browser call returns the new short refusal naming the
  one-tap way back (guided refusal, tab census, and the
  click-anywhere route-death pause).

Discipline (mirrors test_onboarding_mic.py /
test_settings_redesign.py): the GUI reads permission_grants /
settings_voice / browser_enable through the
``voice_assistant.core`` alias, while the flat test world
imports ``core.*`` — two module instances (the dual-import
trap). These tests therefore drive the ALIASED modules — the
ones the GUI code actually reads — and assert on them. The
grants store is pointed at a temp dir per test; the
record-family store is guarded by _DataGuard (consumers-batch
pattern) so Activity lines written during a test are restored
after it. Qt tests need PySide6 (the .venv-qt interpreter);
on an interpreter without it they skip, following the
programme's standing optional-dependency skips.
"""

import json
import os
import pathlib
import re
import sys
import tempfile
import types as _types_mod
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Lab has no PortAudio: stub sounddevice (mirrors
# test_mic_first_use_ask.py / test_micfix.py).
try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001 — lab has no PortAudio
    _fake_sd = _types_mod.ModuleType("sounddevice")
    sys.modules["sounddevice"] = _fake_sd


class _FakeStream:
    """Stand-in for sd.InputStream/OutputStream."""

    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        _FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


class _FakeDefault:
    device = (0, 0)


class _FakeSD:
    InputStream = _FakeStream
    OutputStream = _FakeStream
    default = _FakeDefault()

    @staticmethod
    def query_devices(kind=None):
        devs = [{"name": "Fake Mic", "max_input_channels": 2},
                {"name": "Fake Speaker", "max_input_channels": 0}]
        if kind == "input":
            return {"name": "Fake Mic"}
        return devs


if "sounddevice" in sys.modules and not hasattr(
        sys.modules["sounddevice"], "InputStream"):
    sys.modules["sounddevice"].__dict__.update(_FakeSD.__dict__)

try:
    from PySide6.QtWidgets import QApplication as _QA
    from PySide6.QtWidgets import QLabel as _QLabel
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _QA = None
    _QLabel = None
    _QT = False

# Flat core imports (no Qt, no alias needed): the copy and the
# refusal strings are the same file under either name.
from core import first_run_asks as _asks  # noqa: E402
from core.desktop import browser_cdp as _cdp  # noqa: E402
from core.desktop import browser_session as _bs  # noqa: E402
from core.firstopen_optin import JARGON_TERMS  # noqa: E402

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


def setUpModule():
    """Mirror test_c7_dialog_crop's voice_assistant.* alias.

    gui/* imports voice_assistant.core.* (pre-split paths); the
    tree layout is top-level core/ + gui/, so the alias maps
    voice_assistant -> tree root. The GUI imports happen here,
    after the alias — module-level imports would run before
    it. Snapshot discipline: restore prior entries on teardown
    so a co-run suite's cached modules survive.
    """
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
    """Temp-dir grants store + data/ guard + settings_voice
    reset (mirrors test_settings_redesign). The store override
    lands on the ALIASED module when Qt set it up (the GUI
    classes) and on the flat module on the sys interpreter
    (the core-only classes)."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) \
            / "permission_grants.json"
        # The aliased module when Qt set it up (the GUI classes);
        # the flat module on the sys interpreter, where the alias
        # never exists (the core-only classes — no GUI reads it
        # there, so the flat instance is the right one).
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

    def tearDown(self):
        self._guard.__exit__()
        self._pg.clear_path_override()
        self._pg.clear_session_grants()
        self._tmp.cleanup()
        super().tearDown()


def _jargon_hits(text: str) -> list[str]:
    """Banned jargon terms present in `text` (word-boundary,
    case-insensitive — the first-open scan's own shape)."""
    hits = []
    for term in sorted(JARGON_TERMS):
        if re.search(r"\b" + re.escape(term) + r"\b", text,
                     flags=re.IGNORECASE):
            hits.append(term)
    return hits


# ------------------------------------------------------------------ copy
class CopyTests(unittest.TestCase):
    """Every ask's user-facing text passes the jargon scan."""

    def test_every_ask_string_is_jargon_free(self):
        for text in _asks.all_ask_text():
            self.assertEqual(_jargon_hits(text), [],
                             msg="jargon in ask text: %r" % text)

    def test_ask_order_is_the_ruled_order(self):
        # Round-3 wizard expansion, item 2: home_devices joins
        # the permissions stage last, after files — the ruled
        # order grows by one at the end, nothing else moves.
        self.assertEqual(
            list(_asks.ASK_COPY),
            ["microphone", "browser", "camera", "screen", "files",
             "home_devices"])

    def test_mic_copy_is_the_first_use_dialog_copy(self):
        # The mic ask reuses the first-use dialog's copy: the
        # distinctive phrases must appear in both the copy
        # module and the dialog's own source.
        dialog_src = (ROOT / "gui" / "window_services_confirm.py"
                      ).read_text(encoding="utf-8")
        for phrase in (
                "Always allow switches the microphone on for good",
                "Allow once lets VYUK listen for this session only",
                "it'll ask again next time you start the app"):
            self.assertIn(phrase, _asks.ASK_COPY["microphone"]["body"])
            self.assertIn(phrase, dialog_src)

    def test_screen_ask_does_not_over_claim(self):
        # Plain screen_look needs no ask — the screen ask must
        # say only saving/acting needs it, never that looking
        # once does.
        body = _asks.ASK_COPY["screen"]["body"]
        self.assertIn("Just looking once when you ask doesn't need it",
                      body)
        self.assertNotIn("screenshot", body.lower())

    def test_browser_page_states_the_add_on_comes_later(self):
        # CDP-only first (D5): the page says plainly that VYUK
        # drives Chrome directly for now.
        self.assertIn("add-on comes later",
                      _asks.ASK_COPY["browser"]["direct_line"])
        self.assertIn("separate VYUK profile",
                      _asks.ASK_COPY["browser"]["cost_line"])

    def test_extension_manifest_is_untouched_resobin_only(self):
        # D6 is the owner's call, owed at the follow-up — the
        # manifest's host permissions stay ResoBin-only.
        manifest = json.loads(
            (ROOT / "browser_extension" / "manifest.json"
             ).read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(manifest.get("host_permissions") or []),
            ["https://gymkhana.iitb.ac.in/*",
             "https://resobin.gymkhana.iitb.ac.in/*"])


# ------------------------------------------------------------------ refusals
class RefusalTests(unittest.TestCase):
    """The short refusal names the one-tap way back, with
    Settings as the second way in — never a settings wall."""

    def test_guided_no_session_names_the_tap(self):
        text = _bs._guided_no_session("boom")
        self.assertIn("No controllable browser session is active.",
                      text)
        self.assertIn("Connect my browser", text)
        self.assertIn("Settings → Browser control", text)
        self.assertIn("boom", text)
        self.assertIn(_bs._OPEN_URL_BOUNDARY, text)
        self.assertNotIn("9222", text)
        self.assertNotIn("remote-debugging-port", text)

    def test_real_attach_refusal_names_the_tap(self):
        text = _cdp._REAL_ATTACH_REFUSAL.format(port=9333)
        self.assertIn("Connect my browser", text)
        self.assertIn("Enable browser control", text)
        self.assertIn("9333", text)
        self.assertIn("OFF by default", text)

    def test_status_names_the_tap(self):
        text = _bs._impl_status()
        self.assertIn("Connect my browser", text)
        self.assertIn("Enable browser control", text)

    def test_refusal_strings_are_jargon_free(self):
        # The jargon rule covers the NEW user-facing text: the
        # short refusal and the tab census. The two pre-existing
        # technical refusals keep "opt-in" — they name the
        # mechanism (v5.49) and predate the ask-copy rule; the
        # short refusal is what a fresh install actually hears.
        for text in (_bs._guided_no_session("boom"),):
            self.assertEqual(_jargon_hits(text), [],
                             msg="jargon in refusal: %r" % text)

    def test_dispatch_description_names_the_tap_not_auto_attach(self):
        # The EVALUATED description — what the model actually
        # reads — not the source (adjacent string literals split
        # "Connect my browser" across two lines in the file).
        from core import browser_dispatch as bd

        class _FakeRegistry:
            def __init__(self):
                self.descriptions = {}

            def register(self, name=None, description=None,
                         parameters=None, handler=None, **kwargs):
                self.descriptions[name] = description

        registry = _FakeRegistry()
        bd.register_all(registry)
        desc = registry.descriptions["browser_control"]
        self.assertIn("Connect my browser", desc)
        self.assertIn("Enable browser control", desc)
        self.assertNotIn("attach to your Chrome automatically", desc)
        self.assertIn("open_url window", desc)

    def test_tab_census_names_the_tap(self):
        from core.skills_web6_local import tab_census

        text = tab_census()
        self.assertIn("Connect my browser", text)
        self.assertEqual(_jargon_hits(text), [])


# ------------------------------------------------------------------ fallback
class FallbackTests(unittest.TestCase):
    """A dead browser route names the one-tap way back."""

    def test_click_anywhere_pause_names_the_way_back(self):
        from core.click_anywhere_tool import resolve_route

        for route in ("extension", "cdp"):
            _route, outcome = resolve_route(route)
            self.assertIsNotNone(
                outcome, msg="route %s unexpectedly live" % route)
            self.assertIn("Connect my browser",
                          str(outcome.get("reason", outcome)))

    def test_no_plain_browser_request_calls_enable(self):
        # The enable flow stays GUI-only: no caller in the
        # session/bootstrap/dispatch paths (mirrors
        # test_browser_enable's boundary pin).
        for path in ("core/desktop/browser_session.py",
                     "core/browser_dispatch.py",
                     "core/desktop/browser_cdp.py"):
            src = (ROOT / path).read_text(encoding="utf-8")
            self.assertNotIn("enable_browser_control", src)
            self.assertNotIn("launch_chrome_for_browser_control", src)
            self.assertNotIn("restart_chrome_for_browser_control", src)

# ------------------------------------------------------------------ widget
_CHROME_HS = {"version": {"Browser": "Chrome/139.0.0.0",
                          "Protocol-Version": "1.3"},
              "targets": []}
_STATUS = ("Connected to your Chrome (CDP attach). 3 tab(s) open. "
           "Active tab: 'Example' — https://example.com.")


@unittest.skipUnless(_QT, "Qt unavailable")
class WidgetTests(_StoreMixin):
    """The one-tap connect widget: three steps, every state
    rendered, nothing written on construction."""

    @classmethod
    def setUpClass(cls):
        cls.app = _QA.instance() or _QA([])

    def _fake_enable_env(self, *, connected=False, handshake=None,
                         running=False, port=9222, attach="Attached."):
        """Patch every seam enable_browser_control touches — on
        the ALIASED module, the one the widget reads through the
        facade (mirrors test_browser_enable._fake_enable_env)."""
        patches = [
            mock.patch.object(browser_enable, "_is_connected",
                              return_value=connected),
            mock.patch.object(browser_enable, "_set_opt_in"),
            mock.patch.object(browser_enable, "_port",
                              return_value=port),
            mock.patch.object(browser_enable, "_probe_cdp",
                              return_value=handshake),
            mock.patch.object(browser_enable, "_chrome_running",
                              return_value=running),
            mock.patch.object(browser_enable, "_attach",
                              return_value=attach),
            mock.patch.object(browser_enable, "_status_line",
                              return_value=_STATUS),
            mock.patch.object(
                browser_enable, "_read_devtools_active_port",
                return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return browser_enable

    def _widget(self):
        widget = BrowserConnectWidget()
        self.addCleanup(widget.deleteLater)
        return widget

    def test_construction_writes_nothing(self):
        self.assertFalse(self._store.exists())
        self._widget()
        self.assertFalse(self._store.exists())

    def test_connect_attached_grants_and_flips_opt_in(self):
        self._fake_enable_env(handshake=_CHROME_HS)
        # _attach_and_verify re-reads _is_connected: False at
        # enable time, True after the attach.
        states = [False, True]
        browser_enable._is_connected.side_effect = (
            lambda: states.pop(0) if states else True)
        widget = self._widget()
        result = widget.run_connect_for_tests()
        self.assertEqual(result["state"], "attached")
        # Twice, both True: the tap flips it, then enable flips
        # it again as part of its own contract (idempotent).
        self.assertEqual(
            browser_enable._set_opt_in.call_count, 2)
        browser_enable._set_opt_in.assert_called_with(True)
        self.assertEqual(
            permission_grants.get_state("browser"), "granted")

    def test_connect_renders_attached(self):
        settings_voice.note_browser_no_session()
        widget = self._widget()
        widget._on_connect_result(json.dumps(
            {"state": "attached",
             "message": "Attached to your Chrome. " + _STATUS}))
        self.assertIn("Connected — VYUK can see your Chrome.",
                      widget.status_label.text())
        self.assertIn("3 tab(s) open", widget.status_label.text())
        self.assertEqual(widget.btn_connect.text(), "Connected")
        self.assertFalse(widget.btn_connect.isEnabled())
        self.assertFalse(settings_voice.browser_no_session_active())

    def test_needs_restart_offers_toggle_first(self):
        from core.first_run_asks import TOGGLE_FIRST_LINES

        widget = self._widget()
        widget._on_connect_result(json.dumps(
            {"state": "needs_restart",
             "message": "Chrome is running without the port."}))
        self.assertEqual(widget.status_label.text(),
                         TOGGLE_FIRST_LINES["headline"])
        self.assertFalse(widget.toggle_block.isHidden())
        # The restart is NOT offered yet — the toggle is first.
        self.assertTrue(widget.btn_chrome_action.isHidden())
        self.assertTrue(widget.restart_warning.isHidden())
        widget.btn_restart_instead.click()
        self.assertFalse(widget.restart_warning.isHidden())
        self.assertFalse(widget.btn_chrome_action.isHidden())
        self.assertEqual(widget.btn_chrome_action.text(),
                         "Restart Chrome with browser control")

    def test_needs_launch_offers_launch(self):
        widget = self._widget()
        widget._on_connect_result(json.dumps(
            {"state": "needs_launch",
             "message": "Chrome isn't running."}))
        self.assertFalse(widget.btn_chrome_action.isHidden())
        self.assertEqual(widget.btn_chrome_action.text(),
                         "Open Chrome with browser control")

    def test_failure_offers_try_again_with_the_message_verbatim(self):
        widget = self._widget()
        widget._on_connect_result(json.dumps(
            {"state": "port_in_use",
             "message": "Port 9222 is in use by something else."}))
        self.assertEqual(widget.status_label.text(),
                         "Port 9222 is in use by something else.")
        self.assertFalse(widget.btn_chrome_action.isHidden())
        self.assertEqual(widget.btn_chrome_action.text(), "Try again")

    def test_permission_flip_failure_is_a_state_not_a_crash(self):
        from gui import pf_permissions as _pfp

        widget = self._widget()
        with mock.patch.object(
                _pfp, "apply_permission_changes",
                return_value={"failed": {"browser": "boom"},
                              "changed": [], "results": {},
                              "teardown_registration": None}):
            result = widget.run_connect_for_tests()
        self.assertEqual(result["state"], "failed")
        self.assertIn("Settings → Permissions", result["message"])
        self.assertFalse(self._store.exists())

    def test_dialog_opens_and_double_open_guards(self):
        from PySide6.QtWidgets import QWidget

        owner = QWidget()
        self.addCleanup(owner.deleteLater)
        open_browser_connect_dialog(owner)
        dlg = owner._browser_connect_dialog
        self.assertIsInstance(dlg, BrowserConnectDialog)
        self.assertIsInstance(dlg.widget, BrowserConnectWidget)
        self.assertEqual(
            dlg.widget.btn_connect.text(), "Connect my browser")
        # A second request raises the open one, no new dialog.
        open_browser_connect_dialog(owner)
        self.assertIs(owner._browser_connect_dialog, dlg)
        dlg.btn_done.click()
        _QA.processEvents()
        self.assertIsNone(owner._browser_connect_dialog)


# ------------------------------------------------------------------ wizard
@unittest.skipUnless(_QT, "Qt unavailable")
class WizardTests(_StoreMixin):
    """The wizard's ask sequence: order, grants, skips, gates."""

    @classmethod
    def setUpClass(cls):
        cls.app = _QA.instance() or _QA([])

    def _dialog(self):
        dlg = OnboardingDialog()
        self.addCleanup(dlg.deleteLater)
        return dlg

    def test_no_write_on_construction(self):
        self.assertFalse(self._store.exists())
        self._dialog()
        self.assertFalse(self._store.exists())

    def test_ask_pages_in_ruled_order_and_stage(self):
        dlg = self._dialog()
        order = list(dlg._page_order)
        idx = {w: i for i, w in enumerate(order)}
        self.assertLess(idx[dlg._language_page],
                        idx[dlg._browser_page])
        self.assertLess(idx[dlg._browser_page],
                        idx[dlg._camera_page])
        self.assertLess(idx[dlg._camera_page],
                        idx[dlg._screen_page])
        self.assertLess(idx[dlg._screen_page],
                        idx[dlg._files_page])
        self.assertLess(idx[dlg._files_page],
                        idx[dlg._permissions_page])
        for page in (dlg._browser_page, dlg._camera_page,
                     dlg._screen_page, dlg._files_page):
            self.assertEqual(dlg._stage_of_page(page), "permissions")
            self.assertIn(page, dlg._applicable_pages())
        # The choice-only flow (brain only) skips the asks.
        choice_only = OnboardingDialog(choice_only=True)
        self.addCleanup(choice_only.deleteLater)
        applicable = choice_only._applicable_pages()
        for page in (choice_only._browser_page,
                     choice_only._camera_page,
                     choice_only._screen_page,
                     choice_only._files_page):
            self.assertNotIn(page, applicable)

    def test_mic_ask_shows_on_fresh_install(self):
        dlg = self._dialog()
        self.assertFalse(dlg._mic_ask_block.isHidden())
        self.assertFalse(dlg._audition_btn.isEnabled())
        # Hear-yourself-back stays clickable: its handler reports
        # the honest nothing-recorded state (with the
        # switched-off hint) instead of hiding the explanation.
        self.assertTrue(dlg._hearback_btn.isEnabled())
        self.assertEqual(dlg.mic_state, "denied")
        self.assertEqual(
            sorted(dlg._mic_ask_buttons),
            ["allow_once", "always", "not_now", "settings"])
        self.assertEqual(
            dlg._mic_ask_buttons["always"].text(), "Always allow")
        labels = [lbl.text() for lbl in
                  dlg._mic_ask_block.findChildren(_QLabel)]
        self.assertIn(_asks.ASK_COPY["microphone"]["title"], labels)

    def test_mic_ask_always_grants_durably(self):
        dlg = self._dialog()
        self.assertEqual(dlg._on_mic_ask("always"), "always")
        self.assertEqual(
            permission_grants.get_state("microphone"), "granted")
        self.assertTrue(dlg._mic_ask_block.isHidden())
        self.assertTrue(dlg._audition_btn.isEnabled())
        self.assertEqual(dlg.mic_state, "granted")

    def test_mic_ask_allow_once_is_session_only(self):
        dlg = self._dialog()
        self.assertEqual(dlg._on_mic_ask("allow_once"), "allow_once")
        self.assertTrue(
            permission_grants.has_session_grant("microphone"))
        # No store record: the grant lives in this session only.
        self.assertFalse(self._store.exists())
        self.assertTrue(dlg._audition_btn.isEnabled())
        self.assertEqual(dlg.mic_state, "session")

    def test_mic_ask_not_now_records_a_durable_decline(self):
        # Field-check #9: "Not now" in first-run setup is a
        # durable decision — microphone: denied in the store —
        # so the Connect ask never re-fires. Still not a grant.
        dlg = self._dialog()
        self.assertEqual(dlg._on_mic_ask("not_now"), "not_now")
        self.assertTrue(self._store.exists())
        self.assertEqual(
            permission_grants.get_state("microphone"), "denied")
        self.assertFalse(
            permission_grants.mic_first_use_ask_needed())
        self.assertTrue(dlg._mic_ask_block.isHidden())
        self.assertFalse(dlg._audition_btn.isEnabled())
        self.assertFalse(dlg._mic_off_note.isHidden())

    def test_mic_ask_not_now_save_failure_note_is_visible(self):
        # The durable write can fail (disk); the session decline
        # still covers this session, and the note says the ask
        # will come back. The note lives outside the ask block,
        # so it stays visible after the block hides.
        from gui import pf_permissions as _pfp

        dlg = self._dialog()
        with mock.patch.object(
                _pfp, "apply_permission_changes",
                return_value={"failed": {"microphone": "nope"}}):
            self.assertEqual(dlg._on_mic_ask("not_now"), "not_now")
        self.assertFalse(self._store.exists())
        self.assertFalse(
            permission_grants.mic_first_use_ask_needed())
        self.assertFalse(dlg._mic_note.isHidden())
        self.assertIn("asked again", dlg._mic_note.text())

    def test_mic_ask_goes_through_the_shared_choice(self):
        from gui import pf_permissions as _pfp

        dlg = self._dialog()
        with mock.patch.object(
                _pfp, "apply_mic_first_use_choice",
                return_value="always") as choice:
            self.assertEqual(dlg._on_mic_ask("always"), "always")
        choice.assert_called_once_with(
            "always", notify=dlg._show_mic_note,
            how="first-run setup")

    def test_camera_ask_grants_via_settings_only(self):
        dlg = self._dialog()
        orig = permission_grants.set_permission_state
        vias = []

        def _record(*args, **kwargs):
            vias.append(kwargs.get("via"))
            return orig(*args, **kwargs)

        with mock.patch.object(
                permission_grants, "set_permission_state", _record):
            result = dlg._on_ask_answer(
                dlg._camera_page, "camera", "yes")
        self.assertEqual(result, "granted")
        self.assertEqual(vias, ["settings"])
        self.assertEqual(
            permission_grants.get_state("camera"), "granted")
        self.assertFalse(
            dlg._camera_page._confirm_label.isHidden())
        self.assertIn("Done — the camera is on.",
                      dlg._camera_page._confirm_label.text())

    def test_screen_ask_grants(self):
        dlg = self._dialog()
        result = dlg._on_ask_answer(
            dlg._screen_page, "screen", "yes")
        self.assertEqual(result, "granted")
        self.assertEqual(
            permission_grants.get_state("screen"), "granted")
        self.assertIn("Done — VYUK can keep what she sees.",
                      dlg._screen_page._confirm_label.text())

    def test_camera_skip_writes_nothing(self):
        dlg = self._dialog()
        result = dlg._on_ask_answer(
            dlg._camera_page, "camera", "skip")
        self.assertEqual(result, "skipped")
        self.assertEqual(dlg._camera_page.ask_result, "skipped")
        self.assertFalse(self._store.exists())
        # Fail-closed default on a fresh install (D2 carve-out:
        # camera reads denied with no store) — the skip wrote
        # nothing, so the default stands.
        self.assertEqual(
            permission_grants.get_state("camera"), "denied")
        self.assertFalse(
            dlg._camera_page._confirm_label.isHidden())

    def test_files_ask_chooses_a_folder(self):
        import gui.onboarding_pages as _op

        dlg = self._dialog()
        with mock.patch.object(
                _op, "_pick_folder",
                return_value=self._tmp.name):
            result = dlg._on_ask_answer(
                dlg._files_page, "files", "yes")
        self.assertEqual(result, "folder")
        self.assertEqual(dlg._files_page.ask_result, "folder")
        self.assertEqual(dlg._files_page.chosen_folder,
                         self._tmp.name)
        self.assertIn(self._tmp.name,
                      permission_grants.list_folders())
        # Field-check #8: the folder alone grants nothing — Yes
        # also grants the files permission, or "Done" would lie.
        self.assertEqual(
            permission_grants.get_state("files"), "granted")

    def test_files_ask_grant_failure_is_honest(self):
        # The folder is added but the permission flip fails: the
        # answer is "failed", the grant stays unknown, and the
        # line says the permission didn't switch on — never a
        # "Done" on a half-written grant.
        import gui.onboarding_pages as _op
        from gui import pf_permissions as _pfp

        dlg = self._dialog()
        with mock.patch.object(
                _op, "_pick_folder",
                return_value=self._tmp.name), mock.patch.object(
                _pfp, "apply_permission_changes",
                return_value={"failed": {"files": "nope"}}):
            result = dlg._on_ask_answer(
                dlg._files_page, "files", "yes")
        self.assertEqual(result, "failed")
        self.assertEqual(dlg._files_page.ask_result, "failed")
        self.assertEqual(
            permission_grants.get_state("files"), "unknown")
        self.assertIn(self._tmp.name,
                      permission_grants.list_folders())
        self.assertIn("didn't switch on",
                      dlg._files_page._confirm_label.text())

    def test_files_skip_means_ask_each_time(self):
        dlg = self._dialog()
        before = permission_grants.list_folders()
        result = dlg._on_ask_answer(
            dlg._files_page, "files", "skip")
        self.assertEqual(result, "skipped")
        # Unchanged: no folder was added, so the list is exactly
        # what it was (the defaults on a fresh store — the skip
        # is what makes her ask each time instead).
        self.assertEqual(permission_grants.list_folders(), before)
        self.assertNotIn(self._tmp.name,
                         permission_grants.list_folders())

    def test_files_picker_cancel_is_a_skip(self):
        import gui.onboarding_pages as _op

        dlg = self._dialog()
        before = permission_grants.list_folders()
        with mock.patch.object(_op, "_pick_folder", return_value=""):
            result = dlg._on_ask_answer(
                dlg._files_page, "files", "yes")
        self.assertEqual(result, "skipped")
        self.assertEqual(permission_grants.list_folders(), before)

    def test_ask_pages_render_the_copy(self):
        dlg = self._dialog()
        for page, pid in ((dlg._camera_page, "camera"),
                          (dlg._screen_page, "screen"),
                          (dlg._files_page, "files")):
            self.assertEqual(page.permission_id, pid)
            self.assertEqual(page.ask_result, "unanswered")
            texts = [lbl.text() for lbl in page.findChildren(_QLabel)]
            self.assertIn(_asks.ASK_COPY[pid]["title"], texts)
        self.assertEqual(
            dlg._camera_page._yes_button.text(), "Yes")
        self.assertEqual(
            dlg._files_page._yes_button.text(), "Choose a folder")

    def test_browser_page_renders_the_widget(self):
        # Field-check #45: the page names this machine's path
        # (mocked here for determinism) and still states the
        # CDP-only direct line; the conditional cost line is gone.
        dlg = self._dialog()
        widget = dlg._browser_page.widget
        self.assertIsInstance(widget, BrowserConnectWidget)
        self.assertEqual(widget.btn_connect.text(),
                         "Connect my browser")
        with mock.patch.object(
                browser_enable, "browser_connect_path",
                return_value={"path": "launch",
                              "line": ("On this PC, Chrome isn't "
                                       "running, so connecting will "
                                       "open it on a separate VYUK "
                                       "profile.")}):
            widget._refresh_path_line()
        self.assertEqual(widget.path_label.text(),
                         "On this PC, Chrome isn't running, so "
                         "connecting will open it on a separate "
                         "VYUK profile.")
        texts = [lbl.text() for lbl in widget.findChildren(_QLabel)]
        self.assertTrue(any("add-on comes later" in t
                            for t in texts))
        self.assertFalse(any("If Chrome has to restart" in t
                             for t in texts))

    def test_mic_meter_has_a_visible_label(self):
        # Field-check #44: the level meter carries a visible
        # label, not an accessible name only.
        dlg = self._dialog()
        labels = [lbl for lbl in dlg._mic_page.findChildren(_QLabel)
                  if "watch the bars move" in lbl.text()]
        self.assertEqual(len(labels), 1)
        self.assertFalse(labels[0].isHidden())

    def test_ask_page_hides_when_already_granted(self):
        # Field-check #11: a resumed wizard must not re-ask a
        # decided permission — the ask hides and the state line
        # names it (camera/screen/files).
        permission_grants.set_permission_state(
            "camera", "granted", via="settings", how="Settings")
        permission_grants.set_permission_state(
            "screen", "granted", via="settings", how="Settings")
        permission_grants.set_permission_state(
            "files", "granted", via="settings", how="Settings")
        dlg = self._dialog()
        for page, want in (
                (dlg._camera_page, "already on"),
                (dlg._screen_page, "already keep"),
                (dlg._files_page, "already use")):
            self.assertTrue(page._yes_button.isHidden())
            self.assertTrue(page._skip_button.isHidden())
            self.assertFalse(page._confirm_label.isHidden())
            self.assertIn(want, page._confirm_label.text())

    def test_ask_page_hides_when_denied(self):
        # Decided the other way: a stored denial hides the ask
        # too (the mic page's precedent) and names the way back.
        permission_grants.set_permission_state(
            "camera", "denied", via="settings", how="Settings")
        dlg = self._dialog()
        self.assertTrue(dlg._camera_page._yes_button.isHidden())
        self.assertTrue(dlg._camera_page._skip_button.isHidden())
        self.assertFalse(
            dlg._camera_page._confirm_label.isHidden())
        self.assertIn("switched off",
                      dlg._camera_page._confirm_label.text())

    def test_ask_pages_never_gate_continue(self):
        dlg = self._dialog()
        for page in (dlg._browser_page, dlg._camera_page,
                     dlg._screen_page, dlg._files_page,
                     dlg._permissions_page):
            dlg.pages.setCurrentWidget(page)
            dlg._sync_nav()
            self.assertTrue(
                dlg.btn_next.isEnabled(),
                msg="Continue blocked on %r" % page)
        # Only the brain choice gates Continue.
        dlg.pages.setCurrentWidget(dlg._choice_page)
        dlg.brain_choice = None
        dlg._sync_nav()
        self.assertFalse(dlg.btn_next.isEnabled())

    def test_skip_setup_exits_and_records_nothing(self):
        from PySide6.QtWidgets import QDialog

        dlg = self._dialog()
        self.assertFalse(self._store.exists())
        dlg.btn_skip.click()
        self.assertEqual(dlg.result(), QDialog.Rejected)
        self.assertIsNone(dlg.brain_choice)
        self.assertFalse(self._store.exists())

    def test_skip_setup_keeps_answers_already_given(self):
        # Field-check #10: "Skip setup" exits, but an answer
        # already given stays given — and the nav hint says so.
        from PySide6.QtWidgets import QDialog

        dlg = self._dialog()
        self.assertEqual(dlg._on_ask_answer(
            dlg._camera_page, "camera", "yes"), "granted")
        dlg.btn_skip.click()
        self.assertEqual(dlg.result(), QDialog.Rejected)
        self.assertEqual(
            permission_grants.get_state("camera"), "granted")
        hints = [lbl.text() for lbl in dlg.findChildren(_QLabel)
                 if "Skip setup" in lbl.text()]
        self.assertTrue(any("keeps the answers you already gave" in h
                            for h in hints))

    def test_choice_only_skip_hint_says_nothing_is_kept(self):
        # Choice-only has no ask pages: its hint says the brain
        # choice is not saved, with no "answers already given".
        dlg = OnboardingDialog(choice_only=True)
        self.addCleanup(dlg.deleteLater)
        hints = [lbl.text() for lbl in dlg.findChildren(_QLabel)
                 if "Not now" in lbl.text()
                 or "Skip setup" in lbl.text()]
        self.assertTrue(any("without saving your brain choice" in h
                            for h in hints))
        self.assertFalse(any("keeps the answers" in h
                             for h in hints))


# ------------------------------------------------------------------ D5
class SessionGrantTests(_StoreMixin):
    """A stored denial defeats a session grant (D5)."""

    def _live_origin_module(self):
        """The live_origin module in the SAME namespace as the
        grants module under test (flat on sys, aliased on Qt —
        the dual-import trap: setting the flat origin would not
        move the aliased check)."""
        import importlib

        return importlib.import_module(
            self._pg.__package__ + ".funnel.live_origin")

    def test_stored_denial_defeats_session_grant(self):
        # All three legs of session_grant_allows must hold (grant
        # held + no stored denial + live origin); the denial
        # alone defeats it even with the other two present.
        _lo = self._live_origin_module()
        self._pg.set_permission_state(
            "camera", "denied", via="settings")
        self._pg.grant_session_only("camera")
        token = _lo.set_live_origin()
        try:
            self.assertFalse(
                self._pg.session_grant_allows("camera"))
        finally:
            _lo.reset_live_origin(token)

    def test_session_grant_allows_without_denial(self):
        _lo = self._live_origin_module()
        self._pg.grant_session_only("camera")
        token = _lo.set_live_origin()
        try:
            self.assertTrue(
                self._pg.session_grant_allows("camera"))
        finally:
            _lo.reset_live_origin(token)


# ------------------------------------------------------------------ strip
@unittest.skipUnless(_QT, "Qt unavailable")
class StripTests(_StoreMixin):
    """The strip shows "Connect my browser" after a no-session
    refusal — built on a namespace, no MainWindow (mirrors
    test_settings_redesign.StripTests)."""

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
                     "browser"):
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
        return ns

    def _refresh(self, ns):
        ShellAssistantMixin._refresh_settings_voice_strip(ns)

    def _visible_buttons(self, ns):
        return {name for name in
                ("yes", "no", "undo", "keep", "off", "fix", "browser")
                if getattr(ns, f"btn_settings_voice_{name}").isVisible()}

    def test_browser_no_session_shows_connect(self):
        ns = self._window()
        settings_voice.note_browser_no_session()
        self._refresh(ns)
        self.assertTrue(ns.settings_voice_strip.isVisible())
        self.assertEqual(self._visible_buttons(ns), {"browser"})
        self.assertIn("Connect my browser",
                      ns.settings_voice_strip_label.text())

    def test_clearing_the_flag_hides_the_strip(self):
        # Build 5 (§9.1): the first-launch browser offer is now the
        # lowest-priority strip mode — after the refusal clears, the
        # offer shows if it has never been shown. Suppress it here
        # (offer already shown) so this test isolates the refusal
        # clearing; the offer-after-clear case is pinned by the
        # companion test below.
        ns = self._window()
        settings_voice.note_browser_no_session()
        self._refresh(ns)
        self.assertTrue(ns.settings_voice_strip.isVisible())
        settings_voice.clear_browser_no_session()
        with mock.patch.object(settings_voice, "browser_offer_shown",
                               return_value=True):
            self._refresh(ns)
        self.assertFalse(ns.settings_voice_strip.isVisible())

    def test_clearing_the_flag_shows_the_offer_when_never_shown(self):
        # Build 5 (§9.1): clearing the refusal with the offer never
        # shown shows the one-time offer instead of hiding the strip.
        # The durable write is mocked so the test stays hermetic.
        ns = self._window()
        settings_voice.note_browser_no_session()
        self._refresh(ns)
        self.assertTrue(ns.settings_voice_strip.isVisible())
        settings_voice.clear_browser_no_session()
        with mock.patch.object(settings_voice, "browser_offer_shown",
                               return_value=False), \
             mock.patch.object(settings_voice,
                               "note_browser_offer_shown"), \
             mock.patch.object(browser_enable, "_is_connected",
                               return_value=False):
            self._refresh(ns)
        self.assertTrue(ns.settings_voice_strip.isVisible())
        self.assertEqual(self._visible_buttons(ns), {"browser"})
        self.assertIn("one tap, about a minute",
                      ns.settings_voice_strip_label.text())

    def test_fallback_outranks_browser(self):
        ns = self._window()
        ns._brain_fallback_active = True
        settings_voice.note_browser_no_session()
        self._refresh(ns)
        self.assertEqual(self._visible_buttons(ns), {"fix"})

    def test_proposal_outranks_browser(self):
        from voice_assistant.core import app_settings as _as

        ns = self._window()
        themes = list(_as._themes())
        # app_settings "get" answers in a human sentence
        # ("Theme (her colours): Cyan.") — match by substring,
        # as test_settings_redesign does.
        current = str(_as.app_settings("get", "theme"))
        other = next(t for t in themes if t not in current)
        proposal, _ = settings_voice.propose_setting_change(
            "theme", other)
        self.assertIsNotNone(proposal)
        settings_voice.note_browser_no_session()
        self._refresh(ns)
        self.assertEqual(self._visible_buttons(ns), {"yes", "no"})


if __name__ == "__main__":
    unittest.main()
