"""First-use mic ask (D3, 2026-11-05) — tests.

The fresh-install mic could never open: microphone reads denied with
no store file, and no first-use ask existed (the widget/onboarding
batch owned first-run and never built it). This batch builds the ask:
a GUI dialog at the first human Connect (never voice-resolvable —
the mic is the thing that is off), "Allow once" = a session-only
grant that the mic start() consults, "Open Settings" = the
Permissions page, "Not now" = a per-session decline.

Scope pins (INTENTIONAL — do not "fix" by widening):
- The session grant opens the LIVE-VOICE stream only. The funnel
  (check_tool) never consults it: mic tools keep refusing until
  Settings flips (the D2 consequence). See
  test_session_grant_does_not_unblock_funnel_tools.
- The ask fires ONLY when the store file does not exist (true first
  run). An explicit deny with a store, and a corrupt store, never
  ask (D2's no-ask-again trap). See test_denied_with_store_never_asks
  and test_corrupt_store_never_asks.
- The first-run DEFAULT does not change: mic stays denied until
  allowed. The Settings flip remains the only permanent path.

Lab notes: sounddevice/PortAudio are absent here, so the stream
tests run against a stubbed sounddevice module (mirroring
test_micfix.py's _FakeStream/_FakeSD). The dialog tests need
PySide6 and skip without it, following the programme's standing
optional-dependency skips. The grants store is pointed at a temp
dir per test (the permission_grants test seam), so the real data
dir is never touched.
"""

import json
import os
import pathlib
import sys
import tempfile
import time
import types as _types_mod
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

try:
    import sounddevice  # noqa: F401
except Exception:  # noqa: BLE001 — lab has no PortAudio
    _fake_sd = _types_mod.ModuleType("sounddevice")
    sys.modules["sounddevice"] = _fake_sd

from core import permission_grants as pg  # noqa: E402

try:
    from PySide6.QtCore import Qt as _Qt  # noqa: F401
    from PySide6.QtWidgets import QApplication as _QA
    _QT = True
except Exception:  # noqa: BLE001 — PySide6 absent on sys
    _QA = None
    _QT = False

_APP = None
_VA_SNAPSHOT = None


def setUpModule():
    """Mirror test_c7_dialog_crop's voice_assistant.* alias.

    gui/window_services_confirm.py imports voice_assistant.core.*
    (pre-split paths); the tree layout is top-level core/ + gui/,
    so the alias maps voice_assistant -> tree root. Snapshot
    discipline: restore prior entries on teardown so a co-run
    suite's cached modules survive.
    """
    global _APP, _VA_SNAPSHOT
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


def tearDownModule():
    global _VA_SNAPSHOT
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        del sys.modules[key]
    if _VA_SNAPSHOT is not None:
        sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None


# --- fake sounddevice (mirrors test_micfix.py) ---------------------------


class _FakeStream:
    """Stand-in for sd.InputStream/OutputStream: records instances."""

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
    _sdmod = sys.modules["sounddevice"]
    _sdmod.InputStream = _FakeStream
    _sdmod.OutputStream = _FakeStream
    _sdmod.query_devices = _FakeSD.query_devices
    _sdmod.default = _FakeDefault()


from core.voice import audio as audio_mod  # noqa: E402
from core.voice import audio_devices_mic as audio_mic_mod  # noqa: E402


def _make_manager():
    m = audio_mod.AudioManager()
    m.mic_gain = 1.0
    m.is_muted = False
    return m


class _StoreIsolation(unittest.TestCase):
    """Temp store path + clean session state per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._store = pathlib.Path(self._tmp.name) / "permission_grants.json"
        pg.set_path_override(self._store)
        pg.clear_session_grants()
        self._saved_hooks = list(pg._mic_teardown_hooks)

    def tearDown(self):
        pg.clear_path_override()
        pg.clear_session_grants()
        pg._mic_teardown_hooks[:] = self._saved_hooks
        self._tmp.cleanup()


class SessionGrantTests(_StoreIsolation):
    """The session record + the ask predicate (no Qt needed)."""

    def test_ask_needed_on_fresh_install_denied(self):
        # (a) No store file yet -> mic reads denied -> the ask fires.
        self.assertFalse(self._store.exists())
        self.assertEqual(pg.get_state("microphone"), "denied")
        self.assertTrue(pg.mic_first_use_ask_needed())

    def test_denied_with_store_never_asks(self):
        # (e) An explicit deny WITH a store is not first-run: no ask.
        # INTENTIONAL (D3 scope): the ask is for first-run only, not
        # a nag for users who already said no in Settings.
        self.assertTrue(pg.set_permission_state(
            "microphone", "denied", via="settings"))
        self.assertTrue(self._store.exists())
        self.assertFalse(pg.mic_first_use_ask_needed())

    def test_corrupt_store_never_asks(self):
        # D2's no-ask-again trap: an existing-but-unreadable store
        # never asks, even though the mic reads denied on it.
        self._store.write_text("{not json", encoding="utf-8")
        self.assertEqual(pg.get_state("microphone"), "denied")
        self.assertFalse(pg.mic_first_use_ask_needed())

    def test_granted_store_never_asks(self):
        self.assertTrue(pg.set_permission_state(
            "microphone", "granted", via="settings"))
        self.assertFalse(pg.mic_first_use_ask_needed())

    def test_other_switch_flipped_first_still_asks_for_mic(self):
        # REGRESSION (silent-deaf trap): flipping ANY other switch in
        # Settings creates the store file without a microphone
        # record. get_state() reads that as denied (fail closed), and
        # the old "file exists => not first run" rule meant the mic
        # sat denied with no ask, ever again. A readable store with no
        # microphone record is still "never decided" for the mic.
        self.assertTrue(pg.set_permission_state(
            "screen", "granted", via="settings"))
        self.assertTrue(self._store.exists())
        self.assertEqual(pg.get_state("microphone"), "denied")
        self.assertTrue(pg.mic_first_use_ask_needed())

    def test_other_switch_then_explicit_mic_deny_never_asks(self):
        # The no-nag ruling survives the fix: once the user has
        # made an explicit microphone choice, there is no ask.
        self.assertTrue(pg.set_permission_state(
            "screen", "granted", via="settings"))
        self.assertTrue(pg.set_permission_state(
            "microphone", "denied", via="settings"))
        self.assertFalse(pg.mic_first_use_ask_needed())

    def test_other_switch_then_mic_grant_never_asks(self):
        self.assertTrue(pg.set_permission_state(
            "screen", "granted", via="settings"))
        self.assertTrue(pg.set_permission_state(
            "microphone", "granted", via="settings"))
        self.assertFalse(pg.mic_first_use_ask_needed())

    def test_session_grant_never_persists(self):
        # A session grant writes nothing to the store file: after
        # granting, there is still no store file at all.
        self.assertTrue(pg.grant_session_only("microphone"))
        self.assertFalse(self._store.exists())
        self.assertEqual(pg.get_state("microphone"), "denied")

    def test_session_grant_unknown_id_refused(self):
        self.assertFalse(pg.grant_session_only("not_a_permission"))
        self.assertFalse(pg.has_session_grant("not_a_permission"))

    def test_settings_deny_clears_session_grant(self):
        # D5 switch-off wins: a Settings denial drops any session
        # "Allow once", so a later start() can never reopen the mic
        # on a stale grant.
        self.assertTrue(pg.grant_session_only("microphone"))
        self.assertTrue(pg.has_session_grant("microphone"))
        self.assertTrue(pg.set_permission_state(
            "microphone", "denied", via="settings"))
        self.assertFalse(pg.has_session_grant("microphone"))

    def test_non_settings_flip_cannot_clear_or_write(self):
        # The D8 gate is untouched: a non-settings via writes
        # nothing — and a refused flip leaves the session grant
        # exactly as it was (the clear runs only on a real deny).
        self.assertTrue(pg.grant_session_only("microphone"))
        self.assertFalse(pg.set_permission_state(
            "microphone", "denied", via="voice"))
        self.assertTrue(pg.has_session_grant("microphone"))
        self.assertFalse(self._store.exists())

    def test_decline_suppresses_ask_this_session_only(self):
        # (c) "Not now" declines once per session: the ask does not
        # reappear while this process lives; a new session (the
        # sets cleared, as a fresh process starts) asks again.
        self.assertTrue(pg.mic_first_use_ask_needed())
        pg.decline_session("microphone")
        self.assertTrue(pg.declined_this_session("microphone"))
        self.assertFalse(pg.mic_first_use_ask_needed())
        pg.clear_session_grants()
        self.assertTrue(pg.mic_first_use_ask_needed())

    def test_session_grant_does_not_unblock_funnel_tools(self):
        # INTENTIONAL (D3 scope — do not "fix" by widening): the
        # session grant opens the LIVE-VOICE stream only. The
        # funnel choke point never consults it: mic tools keep
        # refusing until Settings flips (the D2 consequence).
        self.assertTrue(pg.grant_session_only("microphone"))
        allowed, pid = pg.check_tool("mic_check")
        self.assertFalse(allowed)
        self.assertEqual(pid, "microphone")


class MicStartSessionGrantTests(_StoreIsolation):
    """The start() consult: stream opens on session grant, expires."""

    def setUp(self):
        super().setUp()
        self._orig = (audio_mod.sd.InputStream,
                      audio_mod.sd.OutputStream,
                      audio_mod.sd.query_devices,
                      audio_mod.sd.default)
        audio_mod.sd.InputStream = _FakeStream
        audio_mod.sd.OutputStream = _FakeStream
        audio_mod.sd.query_devices = _FakeSD.query_devices
        audio_mod.sd.default = _FakeDefault()
        _FakeStream.instances.clear()
        self._orig_data_dir = audio_mod.DATA_DIR
        self._tmp_data = pathlib.Path(self._tmp.name) / "data"
        audio_mod.DATA_DIR = self._tmp_data
        self._orig_mic_data_dir = audio_mic_mod.DATA_DIR
        audio_mic_mod.DATA_DIR = self._tmp_data

    def tearDown(self):
        (audio_mod.sd.InputStream,
         audio_mod.sd.OutputStream,
         audio_mod.sd.query_devices,
         audio_mod.sd.default) = self._orig
        audio_mod.DATA_DIR = self._orig_data_dir
        audio_mic_mod.DATA_DIR = self._orig_mic_data_dir
        super().tearDown()

    def _input_opened(self, manager):
        return manager._in_stream is not None

    def test_fresh_denied_opens_no_input(self):
        m = _make_manager()
        try:
            m.start()
            self.assertFalse(self._input_opened(m))
            self.assertFalse(m.is_recording)
        finally:
            m.stop()

    def test_allow_once_opens_input_for_the_session(self):
        # (b) "Allow once" opens the stream for the session.
        self.assertTrue(pg.grant_session_only("microphone"))
        m = _make_manager()
        try:
            m.start()
            self.assertTrue(self._input_opened(m))
            self.assertTrue(m.is_recording)
        finally:
            m.stop()

    def test_allow_once_expires_next_session(self):
        # (b, continued) A later start in a NEW session asks again:
        # the grant is gone with the process, so the stream refuses
        # until the ask runs again (or Settings flips).
        self.assertTrue(pg.grant_session_only("microphone"))
        m = _make_manager()
        try:
            m.start()
            self.assertTrue(self._input_opened(m))
        finally:
            m.stop()
        pg.clear_session_grants()
        m2 = _make_manager()
        try:
            m2.start()
            self.assertFalse(self._input_opened(m2))
            self.assertFalse(m2.is_recording)
        finally:
            m2.stop()
        self.assertTrue(pg.mic_first_use_ask_needed())

    def test_settings_grant_opens_without_ask(self):
        # (d) A Settings flip to granted opens the mic permanently,
        # with no ask and no session grant involved.
        self.assertTrue(pg.set_permission_state(
            "microphone", "granted", via="settings"))
        self.assertFalse(pg.mic_first_use_ask_needed())
        self.assertFalse(pg.has_session_grant("microphone"))
        m = _make_manager()
        try:
            m.start()
            self.assertTrue(self._input_opened(m))
            self.assertTrue(m.is_recording)
        finally:
            m.stop()


@unittest.skipUnless(_QT, "PySide6 needed for the dialog tests")
class MicDialogTests(unittest.TestCase):
    """The dialog itself: buttons, safe default, choices."""

    def setUp(self):
        from gui.window_services_confirm import ServicesConfirmMixin
        from PySide6.QtWidgets import QWidget

        class _Host(ServicesConfirmMixin, QWidget):
            pass

        self._host = _Host()
        self._host.show()

    def tearDown(self):
        try:
            self._host.close()
            self._host.deleteLater()
        except Exception:
            pass

    def _buttons_by_text(self, dlg):
        from PySide6.QtWidgets import QPushButton
        found = {}
        for btn in dlg.findChildren(QPushButton):
            found[btn.text()] = btn
        return found

    def test_dialog_has_four_buttons_always_allow_default(self):
        dlg, choice = self._host._build_mic_first_use_dialog()
        try:
            btns = self._buttons_by_text(dlg)
            self.assertEqual(
                set(btns),
                {"Always allow", "Allow once", "Open Settings",
                 "Not now"})
            # "Always allow" holds default + focus: the ask only
            # appears right after a human Connect, and the old
            # "Not now" default meant Enter left a connected-but-deaf
            # session. Focus only lands once the dialog is shown.
            dlg.show()
            _QA.instance().processEvents()
            self.assertTrue(btns["Always allow"].isDefault())
            self.assertTrue(btns["Always allow"].hasFocus())
            self.assertFalse(btns["Not now"].isDefault())
            self.assertEqual(choice, [])
        finally:
            dlg.close()

    def test_click_always_allow(self):
        dlg, choice = self._host._build_mic_first_use_dialog()
        try:
            self._buttons_by_text(dlg)["Always allow"].click()
            self.assertEqual(choice, ["always"])
        finally:
            dlg.close()

    def test_closing_dialog_without_choice_is_not_now(self):
        # Fail-closed is unchanged: no recorded choice (window X,
        # Esc) never grants anything.
        from PySide6.QtCore import QTimer
        host = self._host
        captured = {}
        orig_build = host._build_mic_first_use_dialog

        def _build_and_capture():
            dlg, choice = orig_build()
            captured["dlg"] = dlg
            return dlg, choice

        host._build_mic_first_use_dialog = _build_and_capture
        QTimer.singleShot(0, lambda: captured["dlg"].reject())
        self.assertEqual(host._ask_mic_first_use(), "not_now")

    def test_click_allow_once(self):
        dlg, choice = self._host._build_mic_first_use_dialog()
        try:
            self._buttons_by_text(dlg)["Allow once"].click()
            self.assertEqual(choice, ["allow_once"])
        finally:
            dlg.close()

    def test_click_open_settings(self):
        dlg, choice = self._host._build_mic_first_use_dialog()
        try:
            self._buttons_by_text(dlg)["Open Settings"].click()
            self.assertEqual(choice, ["settings"])
        finally:
            dlg.close()

    def test_click_not_now(self):
        dlg, choice = self._host._build_mic_first_use_dialog()
        try:
            self._buttons_by_text(dlg)["Not now"].click()
            self.assertEqual(choice, ["not_now"])
        finally:
            dlg.close()

    def test_ask_returns_choice_via_exec(self):
        # The exec() path: a timer clicks "Allow once" while the
        # modal loop runs, and _ask_mic_first_use returns it.
        from PySide6.QtCore import QTimer
        host = self._host
        captured = {}

        orig_build = host._build_mic_first_use_dialog

        def _build_and_capture():
            dlg, choice = orig_build()
            captured["dlg"] = dlg
            return dlg, choice

        host._build_mic_first_use_dialog = _build_and_capture

        def _click_allow():
            dlg = captured.get("dlg")
            if dlg is not None:
                self._buttons_by_text(dlg)["Allow once"].click()

        QTimer.singleShot(0, _click_allow)
        self.assertEqual(host._ask_mic_first_use(), "allow_once")


@unittest.skipUnless(_QT, "PySide6 needed for the trigger tests")
class ConnectTriggerTests(_StoreIsolation):
    """_maybe_mic_first_use_ask on a fake window (no MainWindow).

    LAB NAMESPACE NOTE (INTENTIONAL — do not "fix" by switching to
    the flat import): the Connect mixin consults permission_grants
    through the `voice_assistant.core` alias, while the flat test
    world imports `core.permission_grants` — two module instances
    with separate in-memory session sets (the dual-import trap the
    programme's B12/B16/B33 seam lessons cover). In production the
    app runs as the voice_assistant package, so both resolve to the
    one module. These tests therefore drive the ALIASED module —
    the one the GUI code actually reads — and assert on it.
    """

    def setUp(self):
        super().setUp()
        from voice_assistant.core import permission_grants as _pg_a
        self.pga = _pg_a
        self.pga.set_path_override(self._store)
        self.pga.clear_session_grants()

    def tearDown(self):
        self.pga.clear_path_override()
        self.pga.clear_session_grants()
        super().tearDown()

    def _fake_window(self, choice):
        shown = []

        class _Fake:
            pass

        fake = _Fake()
        fake._ask_mic_first_use = lambda: choice
        fake._show_page = lambda index: shown.append(index)
        fake._shown_pages = shown
        return fake

    def _call(self, fake, user_started):
        from gui.window_session_connection import SessionConnectionMixin
        return SessionConnectionMixin._maybe_mic_first_use_ask(
            fake, user_started)

    def test_auto_connect_never_asks(self):
        # Only a HUMAN Connect press asks: auto-reconnect and
        # connect-on-launch never pop a dialog.
        fake = self._fake_window("allow_once")
        self.assertIsNone(self._call(fake, False))
        self.assertFalse(self.pga.has_session_grant("microphone"))

    def test_allow_once_grants_session(self):
        fake = self._fake_window("allow_once")
        self.assertEqual(self._call(fake, True), "allow_once")
        self.assertTrue(self.pga.has_session_grant("microphone"))
        self.assertFalse(self.pga.mic_first_use_ask_needed())
        self.assertEqual(fake._shown_pages, [])

    def test_settings_opens_permissions_page(self):
        fake = self._fake_window("settings")
        self.assertEqual(self._call(fake, True), "settings")
        self.assertEqual(fake._shown_pages, [4])
        self.assertFalse(self.pga.has_session_grant("microphone"))
        self.assertTrue(self.pga.mic_first_use_ask_needed())

    def test_not_now_declines_for_the_session(self):
        fake = self._fake_window("not_now")
        self.assertEqual(self._call(fake, True), "not_now")
        self.assertTrue(self.pga.declined_this_session("microphone"))
        self.assertFalse(self.pga.mic_first_use_ask_needed())
        # A second Connect in the same session does not re-ask.
        self.assertIsNone(self._call(fake, True))

    def test_no_ask_when_store_granted(self):
        self.assertTrue(self.pga.set_permission_state(
            "microphone", "granted", via="settings"))
        calls = []
        fake = self._fake_window("allow_once")
        fake._ask_mic_first_use = lambda: calls.append(True) or "allow_once"
        self.assertIsNone(self._call(fake, True))
        self.assertEqual(calls, [])

    def test_no_ask_when_store_denied(self):
        self.assertTrue(self.pga.set_permission_state(
            "microphone", "denied", via="settings"))
        calls = []
        fake = self._fake_window("allow_once")
        fake._ask_mic_first_use = lambda: calls.append(True) or "allow_once"
        self.assertIsNone(self._call(fake, True))
        self.assertEqual(calls, [])

    def test_always_allow_writes_durable_grant_and_never_asks_again(self):
        # THE annoyance fix: "Always allow" is a durable Settings-path
        # grant, so the next launch (fresh session state) never asks.
        import gui.pf_permissions as pfp
        fake = self._fake_window("always")
        fake._add_system = lambda text: None
        orig_rf = pfp._load_record_family
        pfp._load_record_family = lambda: (_ for _ in ()).throw(
            RuntimeError("no activity log in the test"))
        try:
            self.assertEqual(self._call(fake, True), "always")
        finally:
            pfp._load_record_family = orig_rf
        self.assertEqual(self.pga.get_state("microphone"), "granted")
        self.assertTrue(self.pga.has_session_grant("microphone"))
        # Simulate an app restart: session state gone, store stays.
        self.pga.clear_session_grants()
        self.assertFalse(self.pga.is_mic_denied())
        self.assertFalse(self.pga.mic_first_use_ask_needed())
        calls = []
        fake2 = self._fake_window("allow_once")
        fake2._ask_mic_first_use = lambda: calls.append(1) or "allow_once"
        self.assertIsNone(self._call(fake2, True))
        self.assertEqual(calls, [])

    def test_always_allow_survives_other_switch_already_set(self):
        # The trap + the fix together: another switch was flipped
        # first (store exists, no mic record), the ask still fires,
        # and Always allow then makes the mic granted for good.
        import gui.pf_permissions as pfp
        self.assertTrue(self.pga.set_permission_state(
            "screen", "granted", via="settings"))
        self.assertTrue(self.pga.mic_first_use_ask_needed())
        fake = self._fake_window("always")
        fake._add_system = lambda text: None
        orig_rf = pfp._load_record_family
        pfp._load_record_family = lambda: (_ for _ in ()).throw(
            RuntimeError("no activity log in the test"))
        try:
            self.assertEqual(self._call(fake, True), "always")
        finally:
            pfp._load_record_family = orig_rf
        self.assertEqual(self.pga.get_state("microphone"), "granted")
        self.assertEqual(self.pga.get_state("screen"), "granted")

    def test_always_allow_write_failure_falls_back_to_session(self):
        # If the permanent save fails the user must NOT end up deaf:
        # the session grant still opens the mic, the user is told the
        # save did not stick, and the result is honest ("allow_once").
        import gui.pf_permissions as pfp
        notes = []
        fake = self._fake_window("always")
        fake._add_system = notes.append
        orig = pfp.apply_permission_changes
        pfp.apply_permission_changes = lambda entries, desired, **kw: {
            "changed": [], "failed": {"microphone": "write failed"},
            "results": {}, "teardown_registration": "ok"}
        try:
            self.assertEqual(self._call(fake, True), "allow_once")
        finally:
            pfp.apply_permission_changes = orig
        self.assertTrue(self.pga.has_session_grant("microphone"))
        self.assertTrue(self.pga.is_mic_denied())  # nothing durable
        self.assertEqual(len(notes), 1)
        self.assertIn("Settings", notes[0])

    def test_always_allow_cannot_be_reached_by_a_non_human_connect(self):
        # Auto-reconnect / connect-on-launch still never ask, so
        # "Always allow" can never be triggered without a human.
        fake = self._fake_window("always")
        self.assertIsNone(self._call(fake, False))
        self.assertTrue(self.pga.is_mic_denied())

    def test_start_connection_wiring_pin(self):
        # INTENTIONAL (first-use mic ask): _start_connection must
        # consult the ask on a human Connect and open the mic
        # stream afterwards. This AST pin guards the wiring
        # itself — the fake-window tests above cover the ask's
        # branches; this one proves the Connect path calls them.
        import ast
        src = (ROOT / "gui" / "window_session_connection.py"
               ).read_text(encoding="utf-8")
        tree = ast.parse(src)
        start_conn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_start_connection":
                start_conn = node
        self.assertIsNotNone(start_conn)
        body_src = ast.get_source_segment(src, start_conn) or ""
        self.assertIn("_maybe_mic_first_use_ask(user_started)", body_src)
        self.assertIn("audio_manager.start()", body_src)
        self.assertIn('_mic_choice == "settings"', body_src)


if __name__ == "__main__":
    unittest.main()



class _FakeCaption:
    def __init__(self, text=""):
        self._t = text

    def text(self):
        return self._t

    def setText(self, t):
        self._t = t


@unittest.skipUnless(_QT, "PySide6 needed for the mic-off hint tests")
class MicOffHintTests(_StoreIsolation):
    """_mic_watch_tick names the cause when the mic permission is off.

    Before this, a denied mic opened NO stream (mic_status state
    "no_stream"), which the deaf-session watch ignored (it only
    reacts to "silent"): the session sat connected and deaf with
    nothing on screen — the console print was the only clue."""

    def setUp(self):
        super().setUp()
        from voice_assistant.core import permission_grants as _pg_a
        self.pga = _pg_a
        self.pga.set_path_override(self._store)
        self.pga.clear_session_grants()

    def tearDown(self):
        self.pga.clear_path_override()
        self.pga.clear_session_grants()
        super().tearDown()

    def _host(self, mic_state):
        import gui.window_session_connection as wsc

        class _Worker:
            def is_alive(self):
                return True

        class _Timer:
            def stop(self):
                pass

        class _Host(wsc.SessionConnectionMixin):
            pass

        host = _Host()
        host.worker = _Worker()
        host._mic_watch_timer = _Timer()
        host.caption_label = _FakeCaption("Listening")
        host._deaf_since = None
        host._deaf_hint_active = False
        host._deaf_hint_text = ""
        host._deaf_saved_caption = None
        host._mic_stall_noted = False

        class _AM:
            @staticmethod
            def mic_status():
                return dict(mic_state)

        self._orig_am = wsc.audio_manager
        wsc.audio_manager = _AM()
        self._wsc = wsc
        return host

    def tearDown_am(self):
        self._wsc.audio_manager = self._orig_am

    def test_denied_mic_shows_mic_off_hint_immediately(self):
        host = self._host({"state": "no_stream", "device": None,
                           "level": 0.0})
        try:
            self.assertTrue(self.pga.is_mic_denied())
            host._mic_watch_tick()
            self.assertEqual(host.caption_label.text(),
                             self._wsc._MIC_OFF_HINT)
            self.assertIn("Permissions", host.caption_label.text())
        finally:
            self.tearDown_am()

    def test_hint_restores_caption_once_mic_is_fine(self):
        host = self._host({"state": "no_stream", "device": None,
                           "level": 0.0})
        try:
            host._mic_watch_tick()
            self.assertEqual(host.caption_label.text(),
                             self._wsc._MIC_OFF_HINT)
            # User allows, a healthy stream opens, next tick clears it.
            self.pga.grant_session_only("microphone")
            self._wsc.audio_manager = type("AM", (), {
                "mic_status": staticmethod(lambda: {
                    "state": "ok", "device": "Fake Mic",
                    "level": 0.1})})()
            host._mic_watch_tick()
            self.assertEqual(host.caption_label.text(), "Listening")
        finally:
            self.tearDown_am()

    def test_no_mic_off_hint_when_permission_is_granted(self):
        # A closed stream for some OTHER reason must not be blamed
        # on the permission (no false accusation).
        self.assertTrue(self.pga.set_permission_state(
            "microphone", "granted", via="settings"))
        host = self._host({"state": "no_stream", "device": None,
                           "level": 0.0})
        try:
            host._mic_watch_tick()
            self.assertEqual(host.caption_label.text(), "Listening")
        finally:
            self.tearDown_am()

    def test_session_grant_suppresses_mic_off_hint(self):
        self.pga.grant_session_only("microphone")
        host = self._host({"state": "no_stream", "device": None,
                           "level": 0.0})
        try:
            host._mic_watch_tick()
            self.assertEqual(host.caption_label.text(), "Listening")
        finally:
            self.tearDown_am()

    def test_silent_but_muted_names_the_mute(self):
        # Mic remake (v6.3.x): when the 20 s silence is a MUTE,
        # the hint must say so — the generic device-silence
        # wording used to send the user to Settings for a
        # device that was fine.
        host = self._host({"state": "silent", "muted": True,
                           "device": "Fake Mic", "level": 0.0})
        try:
            host._deaf_since = time.monotonic() - 25.0
            host._mic_watch_tick()
            self.assertEqual(host.caption_label.text(),
                             self._wsc._MIC_MUTED_HINT)
        finally:
            self.tearDown_am()

    def test_silent_not_muted_keeps_device_hint(self):
        # Control: the same 20 s of silence with the mic NOT
        # muted still names the device, not a mute.
        host = self._host({"state": "silent", "muted": False,
                           "device": "Fake Mic", "level": 0.0})
        try:
            host._deaf_since = time.monotonic() - 25.0
            host._mic_watch_tick()
            self.assertIn("Fake Mic", host.caption_label.text())
            self.assertNotIn("muted", host.caption_label.text())
        finally:
            self.tearDown_am()


@unittest.skipUnless(_QT, "PySide6 needed for the connect mute-reset tests")
class ConnectMuteResetTests(unittest.TestCase):
    """_reset_mute_state_for_connect (mic remake v6.3.x):
    transient mute state from the previous session must not
    leak into a (re)connect — a session that ended muted
    used to reconnect deaf while the UI showed LISTENING.
    The mute BUTTON is the source of truth: is_muted is
    synced from it, the PTT toggle resets, and the mic
    button's active property follows. A deliberately-checked
    mute button still mutes."""

    class _Btn:
        def __init__(self, checked=False):
            self._checked = checked
            self.props = {}

        def isChecked(self):
            return self._checked

        def setProperty(self, name, value):
            self.props[name] = value

        def setStyle(self, style):
            pass

        def style(self):
            return None

    def _host(self, mute_checked, is_muted=True, ptt_toggled=True):
        import gui.window_session_connection as wsc

        class _Host(wsc.SessionConnectionMixin):
            pass

        host = _Host()
        host.btn_mute = self._Btn(checked=mute_checked)
        host.btn_mic = self._Btn()
        host._ptt_toggled_on = ptt_toggled
        host._sync_calls = []
        host._sync_muted_label = lambda: host._sync_calls.append(True)
        self._wsc = wsc
        self._orig_am = wsc.audio_manager
        wsc.audio_manager = SimpleNamespace(is_muted=is_muted)
        return host

    def tearDown(self):
        if hasattr(self, "_wsc"):
            self._wsc.audio_manager = self._orig_am
        super().tearDown()

    def test_reset_clears_stale_mute(self):
        host = self._host(mute_checked=False, is_muted=True,
                          ptt_toggled=True)
        host._reset_mute_state_for_connect()
        self.assertFalse(self._wsc.audio_manager.is_muted)
        self.assertFalse(host._ptt_toggled_on)
        self.assertEqual(host.btn_mic.props.get("active"), "true")
        self.assertEqual(len(host._sync_calls), 1)

    def test_reset_keeps_deliberate_mute(self):
        host = self._host(mute_checked=True, is_muted=True,
                          ptt_toggled=True)
        host._reset_mute_state_for_connect()
        self.assertTrue(self._wsc.audio_manager.is_muted)
        self.assertFalse(host._ptt_toggled_on)
        self.assertEqual(host.btn_mic.props.get("active"), "false")
